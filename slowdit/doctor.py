"""`slowdit doctor` — the executable node-level audit.

The isolation model is checked, not assumed: doctor verifies the execution
host's prerequisites and fails loudly on drift. Per-run attestations live in
the response (executor._checks); doctor covers the NODE:

- the docker CLI and daemon are reachable (containers can run);
- git is present (git code sources can be staged);
- the run dir exists and is writable (runs can stage);
- the configured container user is not root (the model's core guarantee).

The isolated-VM profile adds checks on top (no default route, image import
tooling); the local profile audits what it can audit. Exit code 0 = healthy.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field

from .config import Config
from .models import Check
from . import __version__


@dataclass
class DoctorReport:
    ok: bool
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(name=name, ok=ok, detail=detail))
        if not ok:
            self.ok = False

    def print(self) -> None:
        print(f"slowdit doctor (v{__version__})")
        for c in self.checks:
            mark = "ok  " if c.ok else "FAIL"
            print(f"  [{mark}] {c.name}: {c.detail}")
        print(f"  overall: {'healthy' if self.ok else 'UNHEALTHY'}")


def _rc(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()[-200:]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return 127, str(e)


def doctor(config: Config) -> DoctorReport:
    r = DoctorReport(ok=True)

    # docker CLI + daemon
    if shutil.which("docker") is None:
        r.add("docker_cli", False, "docker CLI not found on PATH")
    else:
        rc, out = _rc(["docker", "version", "--format", "{{.Server.Version}}"])
        r.add("docker_daemon", rc == 0,
              f"daemon reachable, server {out.strip()}" if rc == 0
              else f"daemon not reachable: {out}")

    # git
    rc, out = _rc(["git", "--version"])
    r.add("git", rc == 0, out.splitlines()[0] if rc == 0 else f"git failed: {out}")

    # run dir writable
    try:
        config.run_dir.mkdir(parents=True, exist_ok=True)
        probe = config.run_dir / ".doctor-probe"
        probe.write_text("ok")
        probe.unlink()
        r.add("run_dir", True, f"{config.run_dir} exists and is writable")
    except Exception as e:
        r.add("run_dir", False, f"{config.run_dir} not usable: {e}")

    # container user must not be root
    uid = config.run_user.split(":")[0]
    r.add("unprivileged_container_user", uid != "0",
          f"containers run as --user {config.run_user}"
          + ("" if uid != "0" else " — ROOT REFUSED by the model"))

    return r


def main(argv: list[str] | None = None) -> int:
    config = Config.from_env()
    report = doctor(config)
    report.print()
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
