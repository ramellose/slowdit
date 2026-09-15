"""slowdit configuration: env-driven, one config object.

All knobs are environment variables so the server, the CLI and the tests
share one construction path: `Config.from_env()` (or explicit kwargs in
tests). Nothing here is homeserver-specific — a deployment profile is
"config file plus `slowdit serve`".
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


class Config(BaseModel):
    #: Where per-run dirs (code staging + results) live.
    run_dir: Path = Path("/var/tmp/slowdit/runs")
    #: Request timeouts clamp to this ceiling (server-side guardrail).
    max_timeout_seconds: int = Field(default=3600, ge=1)
    #: The container user untrusted code runs as. Fixed uid/gid keeps the
    #: host mapping explicit; "0:0" is refused by the executor.
    run_user: str = "1000:1000"
    #: Keep the run dir on non-COMPLETED outcomes (for debugging).
    keep_failed_runs: bool = True
    #: JUnit file the results dir is expected to contain.
    results_file: str = "results.xml"
    #: Host the container sees in /etc/hosts — none; network is off anyway.
    tmpfs_size: str = "256m"

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Config":
        e = os.environ if env is None else env

        def _int(name: str, default: int) -> int:
            raw = e.get(name)
            return int(raw) if raw else default

        return cls(
            run_dir=Path(e.get("SLOWDIT_RUN_DIR", "/var/tmp/slowdit/runs")),
            max_timeout_seconds=_int("SLOWDIT_MAX_TIMEOUT", 3600),
            run_user=e.get("SLOWDIT_RUN_USER", "1000:1000"),
            keep_failed_runs=e.get("SLOWDIT_KEEP_FAILED_RUNS", "1") == "1",
            results_file=e.get("SLOWDIT_RESULTS_FILE", "results.xml"),
        )
