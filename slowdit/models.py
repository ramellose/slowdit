"""The contract: request and response shapes for a slowdit run.

Two-layer response by design: `outcome` says whether slowdit did its job
(orchestration), `test_result` says what happened inside (the workload).
"the test system failed to run your code" and "your code failed its tests"
are different failures, and callers should react differently to each.

This package is self-contained on purpose: slowdit is a standalone product,
so it carries its own models rather than a shared spine.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

#: The image convention: images without an explicit test_command in the
#: request are expected to carry this entrypoint, which writes JUnit XML to
#: the writable results dir. (The mcp seed images implement it.)
DEFAULT_TEST_COMMAND = "scripts/run_tests.sh --junitxml=/out/results.xml"

#: Where the results JUnit XML lives inside the container's writable dir.
DEFAULT_RESULTS_FILE = "results.xml"


class Outcome(str, Enum):
    """Did slowdit do its job — independent of what ran inside the container."""

    COMPLETED = "completed"  # container ran to completion; a workload result exists
    FETCH_FAILED = "fetch_failed"  # code could not be staged (git/ archive)
    IMAGE_UNAVAILABLE = "image_unavailable"  # pull failed / not importable here
    TIMED_OUT = "timed_out"  # killed by the timeout guard
    NO_RESULTS = "no_results"  # ran fine but produced no parseable JUnit XML
    RUN_FAILED = "run_failed"  # container started but errored (non-zero, crash)

    # NB: COMPLETED means "the sandbox did its job", NOT "the tests passed".


class GitRef(BaseModel):
    """Code as a git URL + ref (branch, tag, or SHA)."""

    url: str
    ref: str = "HEAD"


class CodeSource(BaseModel):
    """The code to run: a git ref or an archive (base64-encoded tar.gz).

    Exactly one of the two must be set.
    """

    git: Optional[GitRef] = None
    archive: Optional[str] = None  # base64-encoded tar.gz (JSON-safe)

    @model_validator(mode="after")
    def _exactly_one(self) -> "CodeSource":
        if (self.git is None) == (self.archive is None):
            raise ValueError(
                "exactly one of git or archive must be set"
            )
        return self

    @property
    def kind(self) -> str:
        return "git" if self.git is not None else "archive"


class RunRequest(BaseModel):
    """What a caller must supply for a run.

    `image` is the full reference (host/org/name:tag) — the image IS the
    environment (dependencies, test runner). Any registry the execution host
    can reach; the host's docker credentials apply (local profile).
    """

    image: str
    code: CodeSource
    #: What to run inside the image (executed as `bash -c <test_command>`).
    #: Optional: images may define the convention (DEFAULT_TEST_COMMAND).
    test_command: Optional[str] = None
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    #: Docker-style limits, e.g. memory_limit="512m", cpu_limit=2.0.
    memory_limit: Optional[str] = None
    cpu_limit: Optional[float] = Field(default=None, gt=0)

    @field_validator("image")
    @classmethod
    def _image_is_full_ref(cls, v: str) -> str:
        v = v.strip()
        if not v or any(c.isspace() for c in v):
            raise ValueError("image must be a non-empty reference without spaces")
        # "full reference": the final component must carry a non-empty
        # explicit tag — no implicit :latest, so runs are reproducible by
        # construction.
        last = v.rsplit("/", 1)[-1]
        if ":" not in last or last.rsplit(":", 1)[1] == "":
            raise ValueError(
                f"image '{v}' has no explicit tag; supply a full reference "
                "(host/org/name:tag)"
            )
        return v


class Check(BaseModel):
    """A per-run attestation: an isolation guarantee, reported with evidence.

    Checks are per-run (returned with every response). The node-level audit
    (the host still matches the model) is `slowdit doctor`, not this.
    """

    name: str
    ok: bool
    detail: Optional[str] = None


class TestFailure(BaseModel):
    """A single failing (or erroring) test case.

    Deliberately small: the nodeid and a short message. The full traceback
    lives in the XML on disk if anyone ever needs it.
    """

    nodeid: str
    message: str
    kind: str  # "failure" or "error"


class TestResult(BaseModel):
    """Layer-2 record: what the tests did, parsed from JUnit XML.

    Only exists when the workload actually ran and wrote a valid XML. A
    missing/malformed XML is the orchestration layer's problem (NO_RESULTS /
    RUN_FAILED), never silently a "0 tests" result here.
    """

    __test__ = False  # not a pytest class (the name would be collected)

    total: int
    failures: int
    errors: int
    skipped: int
    duration_seconds: float
    failing: list[TestFailure] = []

    @property
    def passed(self) -> int:
        # JUnit has no "passed" attribute — it's derived. This is the gotcha.
        return self.total - self.failures - self.errors - self.skipped

    @property
    def ok(self) -> bool:
        # Convenience only — pass/fail semantics belong to the caller.
        return self.failures == 0 and self.errors == 0


class RunResponse(BaseModel):
    """The result of a slowdit run.

    `outcome` + `detail` + `checks` = layer 1 (did slowdit do its job).
    `test_result` = layer 2 (what happened inside), present on COMPLETED.
    """

    run_id: str
    image: str
    resolved_ref: Optional[str] = None  # git SHA when code.git; None for archive
    outcome: Outcome
    exit_code: Optional[int] = None  # raw container exit — layer 1 truth
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    detail: Optional[str] = None
    test_result: Optional[TestResult] = None
    #: Per-run attestations that the isolation guarantees held.
    checks: list[Check] = []
