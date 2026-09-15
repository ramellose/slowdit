"""HTTP transport: status semantics — 200 even when tests fail,
422 for bad requests, /health reflects node state.
"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest

import slowdit.server_http as httpmod
from slowdit.config import Config
from slowdit.models import Outcome, RunResponse, TestResult


class FakeExecutor:
    def __init__(self, response: RunResponse):
        self.response = response

    def run(self, req):
        return self.response


def _resp(outcome=Outcome.COMPLETED, tr: TestResult | None = None) -> RunResponse:
    now = datetime.now(timezone.utc)
    return RunResponse(
        run_id="fake", image="reg/img:1", outcome=outcome, exit_code=0,
        started_at=now, finished_at=now, duration_seconds=0.1,
        test_result=tr, checks=[],
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(httpmod, "_docker_alive", lambda: True)
    monkeypatch.setattr(
        httpmod, "Executor",
        lambda cfg: FakeExecutor(_resp(
            tr=TestResult(total=2, failures=1, errors=0, skipped=0,
                          duration_seconds=0.5)
        )),
    )
    return TestClient(create_app := httpmod.create_app(
        Config(run_dir=tmp_path / "runs")
    ))


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["docker"] is True
    assert body["profile"] == "local"


def test_health_degraded_when_docker_down(tmp_path, monkeypatch):
    monkeypatch.setattr(httpmod, "_docker_alive", lambda: False)
    c = TestClient(httpmod.create_app(Config(run_dir=tmp_path / "r")))
    assert c.get("/health").json()["status"] == "degraded"


def test_run_returns_200_even_when_tests_fail(client):
    """THE semantic: test failure is data in a 200 body, not an HTTP error."""
    r = client.post("/run", json={
        "image": "reg/img:1",
        "code": {"archive": "aGk="},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "completed"          # slowdit did its job
    assert body["test_result"]["failures"] == 1    # but the tests failed


def test_run_rejects_image_without_tag(client):
    r = client.post("/run", json={
        "image": "reg/img",  # no explicit tag
        "code": {"archive": "aGk="},
    })
    assert r.status_code == 422


def test_run_rejects_missing_code(client):
    r = client.post("/run", json={"image": "reg/img:1", "code": {}})
    assert r.status_code == 422
