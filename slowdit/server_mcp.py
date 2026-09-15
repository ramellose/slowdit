"""MCP transport — for agents.

One tool per capability: `run` (the whole contract) and `health` (node
status). Same core as every other transport: the tool is a thin adapter
over Executor.run(), and the tool's docstring IS the contract the agent
sees.
"""

from __future__ import annotations

import json
import os

from mcp.server.fastmcp import FastMCP

from . import __version__
from .config import Config
from .executor import Executor
from .models import RunRequest, RunResponse

mcp = FastMCP(
    "slowdit",
    host=os.environ.get("SLOWDIT_MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("SLOWDIT_MCP_PORT", "8790")),
    instructions=(
        "slowdit executes untrusted code in an isolated, one-shot container "
        "and returns structured results. `run` takes the code (git URL+ref "
        "or base64 tar.gz archive), a full image reference (the image IS "
        "the environment), and an optional test command. The response is "
        "two-layer: `outcome` says whether slowdit did its job; "
        "`test_result` says what happened inside. Decide pass/fail from the "
        "body — `outcome: completed` does NOT mean the tests passed."
    ),
)


def _executor() -> Executor:
    # Built per call so env changes (tests, reloads) are respected; cheap.
    return Executor(Config.from_env())


@mcp.tool()
def run(image: str,
        code_git_url: str | None = None,
        code_git_ref: str = "HEAD",
        code_archive_b64: str | None = None,
        test_command: str | None = None,
        timeout_seconds: int = 300,
        memory_limit: str | None = None,
        cpu_limit: float | None = None) -> str:
    """Run code in an isolated one-shot container and return the result.

    image: full Docker image reference (host/org/name:tag) — the environment.
    code: either code_git_url (+ code_git_ref: branch/tag/SHA) or
    code_archive_b64 (base64-encoded tar.gz).
    test_command: what to run inside the image (default: the image
    convention). timeout_seconds, memory_limit, cpu_limit: resource limits.

    Returns the RunResponse as JSON: outcome, resolved_ref, test_result
    (JUnit counts + failing tests), per-run isolation checks.
    """
    from .models import CodeSource, GitRef

    if (code_git_url is None) == (code_archive_b64 is None):
        return json.dumps({"error": "provide exactly one of code_git_url or code_archive_b64"})
    if code_git_url:
        code = CodeSource(git=GitRef(url=code_git_url, ref=code_git_ref))
    else:
        code = CodeSource(archive=code_archive_b64)

    req = RunRequest(
        image=image,
        code=code,
        test_command=test_command,
        timeout_seconds=timeout_seconds,
        memory_limit=memory_limit,
        cpu_limit=cpu_limit,
    )
    response: RunResponse = _executor().run(req)
    return response.model_dump_json()


@mcp.tool()
def health() -> str:
    """Node-level status: is the execution host healthy (docker reachable)?"""
    import subprocess

    try:
        p = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                           capture_output=True, text=True, timeout=5)
        docker = p.returncode == 0
    except Exception:
        docker = False
    cfg = Config.from_env()
    return json.dumps({
        "status": "ok" if docker else "degraded",
        "version": __version__,
        "profile": "local",
        "docker": docker,
        "run_dir": str(cfg.run_dir),
    })


def serve(transport: str = "stdio") -> None:
    mcp.run(transport=transport)


if __name__ == "__main__":
    serve(os.environ.get("SLOWDIT_MCP_TRANSPORT", "stdio"))
