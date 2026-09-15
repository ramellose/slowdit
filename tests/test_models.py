"""Request/response contract: exclusivity, full image refs, serialization."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from slowdit.models import (
    CodeSource,
    GitRef,
    Outcome,
    RunRequest,
    RunResponse,
    TestResult,
    Check,
)


def test_code_source_exactly_one():
    with pytest.raises(ValidationError, match="exactly one"):
        CodeSource()
    with pytest.raises(ValidationError, match="exactly one"):
        CodeSource(git=GitRef(url="https://x/y.git"), archive="aGk=")
    ok = CodeSource(git=GitRef(url="https://x/y.git", ref="abc123"))
    assert ok.kind == "git"
    assert CodeSource(archive="aGk=").kind == "archive"


@pytest.mark.parametrize("bad", [
    "", "myimage", "reg/org/myimage",          # no explicit tag
    "reg/org/myimage:latest extra",            # spaces
    "reg/org/myimage:",                        # empty tag
])
def test_image_must_be_full_ref(bad):
    with pytest.raises(ValidationError):
        RunRequest(image=bad, code=CodeSource(archive="aGk="))


def test_image_full_ref_ok():
    r = RunRequest(image="registry.example.com/org/img:1.2.3",
                   code=CodeSource(archive="aGk="))
    assert r.image == "registry.example.com/org/img:1.2.3"
    assert r.timeout_seconds == 300


def test_timeout_bounds():
    with pytest.raises(ValidationError):
        RunRequest(image="r/o/i:1", code=CodeSource(archive="aGk="),
                   timeout_seconds=0)
    with pytest.raises(ValidationError):
        RunRequest(image="r/o/i:1", code=CodeSource(archive="aGk="),
                   timeout_seconds=10_000)


def test_outcomes_match_contract():
    assert {o.value for o in Outcome} == {
        "completed", "fetch_failed", "image_unavailable",
        "timed_out", "no_results", "run_failed",
    }


def test_response_serializes_roundtrip():
    tr = TestResult(total=2, failures=1, errors=0, skipped=0,
                    duration_seconds=0.5,
                    failing=[])
    resp = RunResponse(
        run_id="r1", image="r/o/i:1", resolved_ref="abc",
        outcome=Outcome.COMPLETED, exit_code=1,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        duration_seconds=1.0, test_result=tr,
        checks=[Check(name="network_isolated", ok=True, detail="--network=none")],
    )
    data = resp.model_dump(mode="json")
    again = RunResponse.model_validate(data)
    assert again == resp
    assert data["outcome"] == "completed"
    assert data["test_result"]["failures"] == 1
