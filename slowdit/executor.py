"""The core executor: stage → pull → one-shot container → results + checks.

All transports (MCP, HTTP, CLI) speak to `Executor.run()`. The container
invocation is the isolation model, made visible:

    docker run --rm --name slowdit-<run_id>
      --network=none                 # untrusted code gets NO network
      --user 1000:1000               # unprivileged INSIDE
      --cap-drop=ALL
      --security-opt=no-new-privileges:true
      --read-only                    # rootfs is read-only
      --tmpfs /tmp:rw,nosuid,size=…  # bounded scratch (test suites need /tmp)
      --workdir=/code -e PYTHONPATH=/code
      -v <code>:/code:ro             # source read-only
      -v <out>:/out                  # exactly one writable results dir
      <image> bash -c <test_command>

Local profile (v1): the image must be available on the node — present
locally (a local build, or later an offline import) or pulled on demand
from the reference in the request, using the host's docker credentials.
The isolated-VM profile is the same check without the pull: imported
locally or image_unavailable.
"""

from __future__ import annotations

import logging
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .junit import parse_junit_xml
from .models import (
    DEFAULT_TEST_COMMAND,
    Check,
    Outcome,
    RunRequest,
    RunResponse,
)
from .staging import FetchError, clean_run_dir, stage_code

logger = logging.getLogger(__name__)

#: Container exit codes that mean "the workload ran and produced a verdict"
#: (0 = ok, 1 = pytest: some tests failed). Anything else is an error state.
WORKLOAD_RC = (0, 1)


def _tail(s: str | None, n: int = 2000) -> str:
    return (s or "")[-n:]


class Executor:
    """Runs slowdit requests on this host (local profile, v1)."""

    def __init__(self, config: Config):
        self.config = config
        if config.run_user.startswith("0:0"):
            raise ValueError("run_user must not be root (0:0)")

    # ------------------------------------------------------------------ run

    def run(self, req: RunRequest) -> RunResponse:
        cfg = self.config
        started = datetime.now(timezone.utc)
        run_id = f"{started.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        rundir = cfg.run_dir / run_id
        code_dir = rundir / "code"
        out_dir = rundir / "out"
        container_name = f"slowdit-{run_id}"
        timeout = min(req.timeout_seconds, cfg.max_timeout_seconds)

        response = RunResponse(
            run_id=run_id,
            image=req.image,
            started_at=started,
            finished_at=started,
            duration_seconds=0.0,
            outcome=Outcome.RUN_FAILED,
        )

        outcome: Outcome = Outcome.RUN_FAILED
        detail: str | None = None
        exit_code: int | None = None
        test_result = None
        checks: list[Check] = []
        ran_container = False

        try:
            rundir.mkdir(parents=True, exist_ok=True)
            out_dir.mkdir(parents=True, exist_ok=True)
            # The container user (1000) must be able to write results; the
            # run dir is ephemeral, so a world-writable out dir is accepted.
            out_dir.chmod(0o777)

            # 1. Stage the code.
            stage = stage_code(
                req.code.kind,
                code_dir,
                git_url=req.code.git.url if req.code.git else None,
                git_ref=req.code.git.ref if req.code.git else "HEAD",
                archive_b64=req.code.archive,
            )
            response.resolved_ref = stage.resolved_ref

            # 2. Image (local profile): present on the node? Use it —
            # local builds, and later offline imports. Otherwise pull on
            # demand. (The pre-check also keeps local short-name images
            # like slowdit:latest from making `docker pull` hit the hub.)
            pull = None
            if self._docker(["image", "inspect", req.image],
                            timeout=30).returncode != 0:
                pull = self._docker(["pull", req.image], timeout=300)
            if pull is not None and pull.returncode != 0:
                outcome = Outcome.IMAGE_UNAVAILABLE
                detail = (
                    f"docker pull failed: {_tail(pull.stdout) + _tail(pull.stderr)}"
                )
            else:
                image_source = "local" if pull is None else "pulled"
                # 3. One-shot container.
                ran_container = True
                exit_code, output, timed_out = self._run_container(
                    container_name, code_dir, out_dir, req, timeout
                )

                # 4. Verdict: two-layer.
                results_xml = out_dir / cfg.results_file
                if timed_out:
                    outcome = Outcome.TIMED_OUT
                    detail = f"container exceeded {timeout}s and was killed"
                elif exit_code in WORKLOAD_RC and results_xml.exists():
                    outcome = Outcome.COMPLETED
                elif exit_code in WORKLOAD_RC:
                    outcome = Outcome.NO_RESULTS
                    detail = (
                        f"workload exited {exit_code} but produced no "
                        f"{cfg.results_file} — {_tail(output)}"
                    )
                elif results_xml.exists():
                    # Error exit, but the runner still wrote XML: the tests
                    # are data; the error goes in detail.
                    outcome = Outcome.COMPLETED
                    detail = f"container exited {exit_code}: {_tail(output)}"
                else:
                    outcome = Outcome.RUN_FAILED
                    detail = f"container exited {exit_code}: {_tail(output)}"

                if outcome is Outcome.COMPLETED:
                    try:
                        test_result = parse_junit_xml(results_xml)
                    except Exception as e:  # malformed XML is layer 1's concern
                        outcome = Outcome.RUN_FAILED
                        detail = f"results XML unparseable: {e}"
                        test_result = None

                checks = self._checks(req, timed_out, timeout, exit_code,
                                      image_source=image_source,
                                      duration=(datetime.now(timezone.utc) - started).total_seconds())

        except FetchError as e:
            outcome = Outcome.FETCH_FAILED
            detail = f"{e}: {_tail(e.tail)}"
            checks = [
                Check(name="code_staged", ok=False,
                      detail="code staging failed; no container started"),
            ]
        except Exception as e:
            logger.exception("slowdit run crashed")
            outcome = Outcome.RUN_FAILED
            detail = f"executor error: {e}"
            checks = [
                Check(name="executor", ok=False, detail=str(e)),
            ]

        # 5. Finish + teardown (single path: no early returns, no leaks).
        response.outcome = outcome
        response.exit_code = exit_code
        response.detail = detail
        response.test_result = test_result
        response.checks = checks
        response.finished_at = datetime.now(timezone.utc)
        response.duration_seconds = (
            response.finished_at - response.started_at
        ).total_seconds()

        keep = (outcome is not Outcome.COMPLETED) and cfg.keep_failed_runs
        if keep:
            response.detail = (response.detail or "") + f" [run dir kept: {rundir}]"
        else:
            clean_run_dir(rundir)
        return response

    # ------------------------------------------------------------- helpers

    def _run_container(self, container_name: str, code_dir: Path,
                       out_dir: Path, req: RunRequest, timeout: int) -> tuple[int | None, str, bool]:
        """docker run; returns (exit_code, output_tail, timed_out)."""
        cfg = self.config
        test_command = req.test_command or DEFAULT_TEST_COMMAND
        args = [
            "run", "--rm", "--name", container_name,
            "--network=none",
            "--user", cfg.run_user,
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--read-only",
            "--tmpfs", f"/tmp:rw,nosuid,size={cfg.tmpfs_size}",
            "--workdir=/code",
            "-e", "PYTHONPATH=/code",
            "-v", f"{code_dir}:/code:ro",
            "-v", f"{out_dir}:/out",
        ]
        if req.memory_limit:
            args += ["--memory", req.memory_limit]
        if req.cpu_limit:
            args += ["--cpus", str(req.cpu_limit)]
        args += [req.image, "bash", "-c", test_command]

        try:
            proc = subprocess.run(
                ["docker", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return proc.returncode, _tail(proc.stdout) + _tail(proc.stderr), False
        except subprocess.TimeoutExpired as e:
            # The client died; the container may still be running — kill it.
            self._docker(["kill", container_name], timeout=15)
            partial = (e.stdout or b"").decode(errors="replace")
            return None, _tail(partial), True

    def _checks(self, req: RunRequest, timed_out: bool, timeout: int,
                exit_code: int | None, image_source: str,
                duration: float) -> list[Check]:
        """Per-run attestations: what the executor enforced, with evidence.

        These attest the INVOCATION (the flags slowdit applied), not the
        workload — the workload is exactly the untrusted part. The node-level
        audit (the host still matches the model) is `slowdit doctor`.
        """
        c = self.config
        return [
            Check(
                name="image_source", ok=True,
                detail=(
                    "image present on the node (no pull attempted)"
                    if image_source == "local"
                    else "image pulled from registry"
                ),
            ),
            Check(
                name="network_isolated", ok=True,
                detail="invocation used --network=none",
            ),
            Check(
                name="unprivileged_user", ok=True,
                detail=f"invocation used --user {c.run_user}",
            ),
            Check(
                name="no_privilege_escalation", ok=True,
                detail="--cap-drop=ALL --security-opt=no-new-privileges:true --read-only",
            ),
            Check(
                name="mounts_in_scope", ok=True,
                detail="code at /code:ro; writable: /out (results), /tmp (bounded tmpfs)",
            ),
            Check(
                name="limits_applied", ok=not timed_out,
                detail=(
                    f"timeout {timeout}s (memory {req.memory_limit or 'none'}, "
                    f"cpus {req.cpu_limit or 'none'})"
                    + (" — EXCEEDED" if timed_out else "")
                ),
            ),
            Check(
                name="duration", ok=True,
                detail=(
                    f"{duration:.1f}s elapsed "
                    f"(exit {exit_code if exit_code is not None else 'killed'})"
                ),
            ),
        ]

    def _docker(self, args: list[str], timeout: int) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )


def outcome_needs_debug(outcome: Outcome) -> bool:
    """COMPLETED is the only outcome whose run dir is always safe to drop."""
    return outcome is not Outcome.COMPLETED
