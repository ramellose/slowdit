"""HTTP transport: POST /run (synchronous) + GET /health — for CI systems.

HTTP status semantics (deliberate, same as the contract):
  - 200: the WEBHOOK succeeded — it ran the executor and got a RunResponse,
    REGARDLESS of whether the tests passed or the orchestration failed.
    Test failure and orchestration failure are DATA in the body, not HTTP
    errors. The caller decides pass/fail from the body, not the status code.
  - 422: the request itself was invalid (bad shape, not a full image ref).
  - 500: the webhook ITSELF broke. "The test system is broken" — distinct
    from "your tests failed" (that is a 200 with a non-completed outcome).

SYNCHRONOUS BY DESIGN (for now): the request stays open for the whole run.
Fine while runs are short; if runs grow past HTTP timeout ceilings this
becomes async (job id + status endpoint). Documented tradeoff, not an
accident.
"""

from __future__ import annotations

import logging
import subprocess

from fastapi import FastAPI, HTTPException

from . import __version__
from .config import Config
from .executor import Executor
from .models import RunRequest, RunResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _docker_alive(timeout: float = 5.0) -> bool:
    try:
        p = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0
    except Exception:
        return False


def create_app(config: Config) -> FastAPI:
    """App factory: config in, app out (keeps tests off module globals)."""
    app = FastAPI(title="slowdit", version=__version__)
    executor = Executor(config)

    @app.get("/health")
    def health():
        """Node-level status. Pass/fail of runs is NOT this endpoint's job."""
        docker = _docker_alive()
        return {
            "status": "ok" if docker else "degraded",
            "version": __version__,
            "profile": "local",
            "docker": docker,
            "run_dir": str(config.run_dir),
        }

    @app.post("/run", response_model=RunResponse)
    def run(req: RunRequest):
        try:
            return executor.run(req)
        except Exception as e:
            logger.exception("slowdit run crashed")
            raise HTTPException(status_code=500, detail=f"webhook error: {e}")

    return app


#: Module-level app for `uvicorn slowdit.server_http:app` and direct imports.
app = create_app(Config.from_env())
