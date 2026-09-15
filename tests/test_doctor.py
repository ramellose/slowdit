"""doctor: healthy node passes, missing docker fails loudly."""

import os
import stat

from slowdit.config import Config
from slowdit.doctor import doctor

FAKE_DOCKER = """#!/usr/bin/env bash
case "$1" in
  version) echo "v1.0-fake" ;;
  info) exit 0 ;;
esac
exit 0
"""


def _fake_bin(tmp_path, name: str = "docker", script: str = FAKE_DOCKER):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(script)
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return d


def test_doctor_healthy(tmp_path, monkeypatch):
    bin_dir = _fake_bin(tmp_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    report = doctor(Config(run_dir=tmp_path / "runs"))
    assert report.ok, [c for c in report.checks if not c.ok]
    names = {c.name for c in report.checks}
    assert {"docker_daemon", "git", "run_dir",
            "unprivileged_container_user"} <= names
    assert all(c.ok for c in report.checks)


def test_doctor_fails_without_docker(tmp_path, monkeypatch):
    empty = tmp_path / "emptybin"
    empty.mkdir()
    # keep git available (it's a real prerequisite), drop docker:
    git = os.path.realpath("git")
    gitbin = tmp_path / "gitbin"
    gitbin.mkdir()
    import shutil
    (gitbin / "git").symlink_to(git)
    monkeypatch.setenv("PATH", f"{empty}{os.pathsep}{gitbin}")
    report = doctor(Config(run_dir=tmp_path / "runs"))
    assert not report.ok
    docker_check = next(c for c in report.checks if c.name == "docker_cli")
    assert not docker_check.ok


def test_doctor_refuses_root_container_user(tmp_path):
    report = doctor(Config(run_dir=tmp_path / "runs", run_user="0:0"))
    assert not report.ok
    c = next(x for x in report.checks if x.name == "unprivileged_container_user")
    assert not c.ok
