"""Staging: archive extraction with traversal guards; dispatch."""

import base64
import io
import tarfile

import pytest

from slowdit.staging import FetchError, stage_archive, stage_code


def _tar_bytes(members: list[tuple[str, bytes | None]]) -> bytes:
    """members: (name, content-or-None-for-dir)"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in members:
            if content is None:
                ti = tarfile.TarInfo(name)
                ti.type = tarfile.DIRTYPE
                tf.addfile(ti)
            else:
                ti = tarfile.TarInfo(name)
                ti.size = len(content)
                tf.addfile(ti, io.BytesIO(content))
    return buf.getvalue()


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def test_archive_happy_path(tmp_path):
    src = _b64(_tar_bytes([
        ("", None),
        ("hello.txt", b"hi"),
        ("sub/inner.txt", b"inner"),
    ]))
    code = tmp_path / "code"
    result = stage_archive(src, code)
    assert result.resolved_ref is None
    assert (code / "hello.txt").read_text() == "hi"
    assert (code / "sub" / "inner.txt").read_text() == "inner"


@pytest.mark.parametrize("evil", [
    ("../escape.txt", b"pwn"),
    ("/abs/escape.txt", b"pwn"),
    ("a/../../escape2.txt", b"pwn"),
])
def test_archive_traversal_rejected(tmp_path, evil):
    src = _b64(_tar_bytes([("ok.txt", b"fine"), evil]))
    with pytest.raises(FetchError):
        stage_archive(src, tmp_path / "code")


def test_archive_absolute_symlink_rejected(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        ti = tarfile.TarInfo("link")
        ti.type = tarfile.SYMTYPE
        ti.linkname = "/etc/passwd"
        tf.addfile(ti)
    with pytest.raises(FetchError, match="symlink escapes"):
        stage_archive(_b64(buf.getvalue()), tmp_path / "code")


def test_archive_internal_symlink_allowed(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        ti = tarfile.TarInfo("real.txt")
        data = b"x"
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
        sl = tarfile.TarInfo("alias")
        sl.type = tarfile.SYMTYPE
        sl.linkname = "real.txt"  # internal relative — fine
        tf.addfile(sl)
    code = tmp_path / "code"
    stage_archive(_b64(buf.getvalue()), code)
    assert (code / "alias").read_text() == "x"


def test_archive_hardlink_rejected(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        ti = tarfile.TarInfo("real.txt")
        ti.size = 1
        tf.addfile(ti, io.BytesIO(b"x"))
        hl = tarfile.TarInfo("hard")
        hl.type = tarfile.LNKTYPE
        hl.linkname = "real.txt"
        tf.addfile(hl)
    with pytest.raises(FetchError, match="hard link"):
        stage_archive(_b64(buf.getvalue()), tmp_path / "code")


def test_archive_bad_b64(tmp_path):
    with pytest.raises(FetchError, match="base64"):
        stage_archive("not!valid$b64", tmp_path / "code")


def test_archive_not_a_tar(tmp_path):
    with pytest.raises(FetchError, match="tar.gz"):
        stage_archive(_b64(b"definitely not a tarball"), tmp_path / "code")


def test_dispatch_unknown_kind(tmp_path):
    with pytest.raises(FetchError, match="unknown code source kind"):
        stage_code("carrier-pigeon", tmp_path / "code")
