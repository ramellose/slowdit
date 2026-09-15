"""Code staging: get the caller's code onto the execution host.

Two sources: git (URL + ref → shallow clone, detached checkout, resolved
SHA) or an archive (base64 tar.gz → guarded extraction). The staging dir is
per-run and torn down with the run; nothing here executes caller code.
"""

from __future__ import annotations

import base64
import io
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path


class FetchError(RuntimeError):
    """The code could not be staged. `tail` carries the last lines of output."""

    def __init__(self, message: str, tail: str = ""):
        super().__init__(message)
        self.tail = tail


def _tail(s: str | None, n: int = 2000) -> str:
    return (s or "")[-n:]


@dataclass
class StageResult:
    code_dir: Path  # the extracted repo root, to be mounted at /code
    resolved_ref: str | None  # git SHA, or None for archives


def _git(*args: str, cwd: str | Path | None = None, timeout: int = 120) -> str:
    """Run git, return stdout; raise FetchError with a tail on failure."""
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise FetchError("git timed out", _tail((e.stdout or b"").decode(errors="replace")))
    if p.returncode != 0:
        raise FetchError(
            f"git {' '.join(args[:3])}… failed (rc={p.returncode})",
            _tail(p.stdout) + "\n" + _tail(p.stderr),
        )
    return p.stdout


def stage_git(url: str, ref: str, code_dir: Path) -> StageResult:
    """Clone `url` at `ref` into `code_dir`; resolve the immutable SHA.

    Shallow clone where possible; a SHA ref needs history, so we fall back
    to a full clone only when the ref is not branch-like. v1 keeps it simple:
    clone (full), detach-checkout the ref — repos are small, correctness first.
    """
    code_dir.parent.mkdir(parents=True, exist_ok=True)
    _git("clone", "--", url, str(code_dir))
    _git("checkout", "--detach", ref, cwd=code_dir)
    resolved = _git("rev-parse", "HEAD", cwd=code_dir).strip()
    return StageResult(code_dir=code_dir, resolved_ref=resolved)


def stage_archive(b64: str, code_dir: Path) -> StageResult:
    """Extract a base64 tar.gz into `code_dir` with path-traversal guards.

    The archive is untrusted input: member names are vetted, and anything
    that would escape the destination (absolute paths, `..`, symlink/hardlink
    targets pointing outside) is rejected before a single byte is written.
    """
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as e:
        raise FetchError(f"archive is not valid base64: {e}")

    code_dir.parent.mkdir(parents=True, exist_ok=True)
    code_dir.mkdir(parents=True, exist_ok=True)
    root = code_dir.resolve()

    try:
        tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
    except (tarfile.TarError, EOFError) as e:
        raise FetchError(f"archive is not a readable tar.gz: {e}")

    with tf:
        members = tf.getmembers()
        # Vet everything BEFORE extracting anything.
        for m in members:
            target = (root / m.name).resolve() if not m.name.startswith("/") else None
            if m.name.startswith("/") or ".." in m.name.split("/"):
                raise FetchError(f"archive member escapes the destination: {m.name}")
            if m.islnk():
                raise FetchError(f"archive contains a hard link: {m.name}")
            if m.issym() and (
                m.linkname.startswith("/") or ".." in Path(m.linkname).parts
            ):
                # Internal relative symlinks are fine; escapes are not.
                raise FetchError(
                    f"symlink escapes the destination: {m.name} -> {m.linkname}"
                )
            if not (m.isdir() or m.isfile() or m.issym()):
                raise FetchError(f"archive member type not allowed: {m.name}")
        # `filter="data"` is the belt to our vetting's braces: it re-checks
        # every member on extraction (absolute paths, .., dangerous links).
        tf.extractall(path=root, filter="data")
    return StageResult(code_dir=code_dir, resolved_ref=None)


def stage_code(kind: str, code_dir: Path, git_url: str | None = None,
               git_ref: str = "HEAD", archive_b64: str | None = None) -> StageResult:
    """Dispatch on source kind. `code_dir` must not exist yet (caller owns it)."""
    if kind == "git":
        if not git_url:
            raise FetchError("git source requires a url")
        return stage_git(git_url, git_ref, code_dir)
    if kind == "archive":
        if not archive_b64:
            raise FetchError("archive source requires the archive payload")
        return stage_archive(archive_b64, code_dir)
    raise FetchError(f"unknown code source kind: {kind}")


def clean_run_dir(path: Path) -> None:
    """Tear down a per-run dir (idempotent)."""
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
