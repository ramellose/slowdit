"""Executor: the full two-layer verdict matrix, with a FAKE docker on PATH.

The fake docker emulates pull/run/kill and writes the JUnit file the way a
real seed image would — so the executor's container logic (arg building,
verdict matrix, teardown) is exercised end-to-end without a daemon.
"""

import base64
import io
import os
import tarfile

import pytest

from slowdit.config import Config
from slowdit.executor import Executor
from slowdit.models import CodeSource, Outcome, RunRequest

FAKE_DOCKER = r"""#!/usr/bin/env bash
args=("$@")
printf '%s\n' "$*" >> "${FAKE_DOCKER_LOG:-/dev/null}"
sub="${args[0]}"
case "$sub" in
  image)
    # only reg/local:1 is "present on the node"
    if [[ "${args[1]}" == "inspect" && "${args[2]}" == "reg/local:1" ]]; then
      exit 0
    fi
    exit 1
    ;;
  pull)
    img="${args[1]}"
    if [[ "$img" == *missing* ]]; then
      echo "Error: pull access denied for $img" >&2
      exit 1
    fi
    exit 0
    ;;
  run)
    outdir=""; cmd=""
    for ((i=1; i<${#args[@]}; i++)); do
      if [[ "${args[i-1]}" == "-v" && "${args[i]}" == *":/out" ]]; then
        outdir="${args[i]%%:*}"
      fi
      if [[ "${args[i]}" == "bash" ]]; then
        cmd="${args[i+2]}"
      fi
    done
    if [[ "$cmd" == *sleep* ]]; then sleep 5; exit 143; fi
    if [[ "$cmd" == *noresults* ]]; then echo "ran, wrote nothing"; exit 0; fi
    if [[ "$cmd" == *crash* ]]; then echo "kaboom" >&2; exit 3; fi
    if [[ "$cmd" == *fail* ]]; then
      cat > "$outdir/results.xml" <<'EOF'
<testsuite tests="2" failures="1" errors="0" skipped="0" time="1">
  <testcase classname="a" name="ok"></testcase>
  <testcase classname="a" name="bad"><failure message="nope">tb</failure></testcase>
</testsuite>
EOF
      exit 1
    fi
    cat > "$outdir/results.xml" <<'EOF'
<testsuites><testsuite tests="3" failures="0" errors="0" skipped="0" time="2">
  <testcase classname="a" name="o1"></testcase>
  <testcase classname="a" name="o2"></testcase>
  <testcase classname="a" name="o3"></testcase>
</testsuite></testsuites>
EOF
    exit 0
    ;;
  kill)
    echo "killed ${args[1]}" >> "${FAKE_KILL_LOG:-/dev/null}"
    exit 0
    ;;
  info)
    exit 0
    ;;
esac
exit 0
"""


def _archive_b64() -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        ti = tarfile.TarInfo("hello.txt")
        data = b"hi"
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    return base64.b64encode(buf.getvalue()).decode()


def _req(test_command=None, image="reg/img:1", archive=True,
         timeout=30, **kw) -> RunRequest:
    code = CodeSource(archive=_archive_b64()) if archive else None
    return RunRequest(image=image, code=code, test_command=test_command,
                      timeout_seconds=timeout, **kw)


@pytest.fixture
def executor(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(FAKE_DOCKER)
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    cfg = Config(run_dir=tmp_path / "runs", keep_failed_runs=True)
    return Executor(cfg), cfg


def test_completed(tmp_path, executor, monkeypatch):
    ex, cfg = executor
    log = tmp_path / "docker.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    resp = ex.run(_req())
    assert resp.outcome is Outcome.COMPLETED
    assert resp.exit_code == 0
    assert resp.resolved_ref is None  # archive source
    assert resp.test_result is not None
    assert resp.test_result.total == 3 and resp.test_result.ok
    assert resp.detail is None
    # per-run attestations present, all ok
    names = {c.name for c in resp.checks}
    assert {"image_source", "network_isolated", "unprivileged_user",
            "no_privilege_escalation", "mounts_in_scope", "limits_applied",
            "duration"} <= names
    assert all(c.ok for c in resp.checks)
    # reg/img:1 is not on the node → pulled
    src = next(c for c in resp.checks if c.name == "image_source")
    assert "pulled" in src.detail
    assert "pull reg/img:1" in log.read_text()
    # COMPLETED → run dir cleaned
    assert list(cfg.run_dir.iterdir()) == []


def test_image_local(tmp_path, executor, monkeypatch):
    # A local short-name image (no registry component) must be used as-is:
    # the pre-check finds it on the node, so no pull is attempted (a bare
    # `docker pull` would hit the hub and fail).
    ex, cfg = executor
    log = tmp_path / "docker.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    resp = ex.run(_req(image="reg/local:1"))
    assert resp.outcome is Outcome.COMPLETED
    src = next(c for c in resp.checks if c.name == "image_source")
    assert "present on the node" in src.detail
    invocations = log.read_text()
    assert "image inspect reg/local:1" in invocations
    assert "pull" not in invocations


def test_completed_failed_tests(tmp_path, executor):
    ex, cfg = executor
    resp = ex.run(_req(test_command="run fail"))
    # Layer 1: slowdit did its job. Layer 2: the tests failed.
    assert resp.outcome is Outcome.COMPLETED
    assert resp.exit_code == 1
    assert resp.test_result.failures == 1
    assert resp.test_result.failing[0].nodeid == "a::bad"
    assert not resp.test_result.ok
    # still cleaned (the workload's failure is not a run failure)
    assert list(cfg.run_dir.iterdir()) == []


def test_no_results(tmp_path, executor):
    ex, cfg = executor
    resp = ex.run(_req(test_command="noresults"))
    assert resp.outcome is Outcome.NO_RESULTS
    assert resp.exit_code == 0
    assert resp.test_result is None
    assert "results.xml" in (resp.detail or "")
    # kept for debugging
    assert len(list(cfg.run_dir.iterdir())) == 1


def test_run_failed(tmp_path, executor):
    ex, cfg = executor
    resp = ex.run(_req(test_command="crash"))
    assert resp.outcome is Outcome.RUN_FAILED
    assert resp.exit_code == 3
    assert resp.test_result is None
    assert "kaboom" in (resp.detail or "")
    assert len(list(cfg.run_dir.iterdir())) == 1


def test_image_unavailable(tmp_path, executor):
    ex, cfg = executor
    resp = ex.run(_req(image="reg/missing:1"))
    assert resp.outcome is Outcome.IMAGE_UNAVAILABLE
    assert "pull" in (resp.detail or "")
    # no image → no container → no per-run invocation checks
    assert resp.checks == []
    assert len(list(cfg.run_dir.iterdir())) == 1


def test_bad_archive_fetch_failed(tmp_path, executor):
    ex, cfg = executor
    req = RunRequest(image="reg/img:1", code=CodeSource(archive="!!not b64!!"))
    resp = ex.run(req)
    assert resp.outcome is Outcome.FETCH_FAILED
    assert resp.checks and resp.checks[0].name == "code_staged"
    assert not resp.checks[0].ok


def test_timed_out(tmp_path, executor, monkeypatch):
    ex, cfg = executor
    kill_log = tmp_path / "kills.log"
    monkeypatch.setenv("FAKE_KILL_LOG", str(kill_log))
    resp = ex.run(_req(test_command="sleep 5", timeout=1))
    assert resp.outcome is Outcome.TIMED_OUT
    assert resp.exit_code is None
    limits = next(c for c in resp.checks if c.name == "limits_applied")
    assert not limits.ok
    assert "EXCEEDED" in limits.detail
    # the executor killed the runaway container
    assert kill_log.exists() and "killed" in kill_log.read_text()
    assert len(list(cfg.run_dir.iterdir())) == 1


def test_root_user_refused(tmp_path):
    with pytest.raises(ValueError, match="root"):
        Executor(Config(run_dir=tmp_path, run_user="0:0"))
