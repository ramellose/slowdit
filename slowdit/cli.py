"""slowdit CLI — for humans and test plans.

    slowdit run --image REG/img:tag --git URL --ref <sha|branch> \
                [--test-cmd CMD] [--timeout 300] [--memory 512m] [--cpus 2]
    slowdit run --image REG/img:tag --archive repo.tar.gz ...
    slowdit serve --http [--host 0.0.0.0] [--port 8080]
    slowdit serve --mcp  [--transport stdio|sse]
    slowdit doctor

`run` executes synchronously and prints the full RunResponse JSON — the same
body the HTTP transport returns. Pass/fail semantics are the caller's.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

from . import __version__
from .config import Config
from .doctor import doctor
from .executor import Executor
from .models import CodeSource, GitRef, RunRequest


def _build_request(args: argparse.Namespace) -> RunRequest:
    if args.git:
        code = CodeSource(git=GitRef(url=args.git, ref=args.ref or "HEAD"))
    elif args.archive:
        raw = Path(args.archive).read_bytes()
        code = CodeSource(archive=base64.b64encode(raw).decode())
    else:
        raise SystemExit("error: provide --git URL or --archive FILE")

    return RunRequest(
        image=args.image,
        code=code,
        test_command=args.test_cmd,
        timeout_seconds=args.timeout,
        memory_limit=args.memory,
        cpu_limit=args.cpus,
    )


def cmd_run(args: argparse.Namespace) -> int:
    req = _build_request(args)
    executor = Executor(Config.from_env())
    response = executor.run(req)
    print(json.dumps(response.model_dump(mode="json"), indent=2))
    # Exit code mirrors the OUTCOME, not the tests: 0 = slowdit did its job,
    # 1 = orchestration failed. Test pass/fail is in the body (layer 2).
    return 0 if response.outcome.value == "completed" else 1


def cmd_serve(args: argparse.Namespace) -> int:
    if args.http:
        import uvicorn

        from .server_http import create_app

        app = create_app(Config.from_env())
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        from .server_mcp import serve

        serve(transport=args.transport)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    report = doctor(Config.from_env())
    report.print()
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="slowdit",
        description="Execute untrusted code in an isolated environment.",
    )
    parser.add_argument("--version", action="version", version=f"slowdit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run code in a one-shot container")
    p_run.add_argument("--image", required=True,
                       help="full image reference (host/org/name:tag)")
    src = p_run.add_mutually_exclusive_group(required=True)
    src.add_argument("--git", help="git URL of the code")
    src.add_argument("--archive", help="path to a tar.gz of the code")
    p_run.add_argument("--ref", help="git ref: branch, tag, or SHA (default HEAD)")
    p_run.add_argument("--test-cmd", help="command to run in the image "
                                          "(default: the image convention)")
    p_run.add_argument("--timeout", type=int, default=300, help="seconds")
    p_run.add_argument("--memory", help='docker memory limit, e.g. "512m"')
    p_run.add_argument("--cpus", type=float, help="docker CPU limit, e.g. 2")
    p_run.set_defaults(func=cmd_run)

    p_serve = sub.add_parser("serve", help="serve the transports")
    mode = p_serve.add_mutually_exclusive_group()
    mode.add_argument("--http", action="store_true", help="HTTP transport")
    mode.add_argument("--mcp", action="store_true", help="MCP transport (default)")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8080)
    p_serve.add_argument("--transport", default="stdio", choices=["stdio", "sse"],
                         help="MCP transport")
    p_serve.set_defaults(func=cmd_serve)

    p_doc = sub.add_parser("doctor", help="audit the execution host")
    p_doc.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
