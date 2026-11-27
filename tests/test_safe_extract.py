# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""The hardened extractor: every attack must fail with a stable code and leave NOTHING behind."""
import io
import os
import stat
import struct
import tarfile
import zipfile
import zlib
from pathlib import Path

import pytest

from app.safe_extract import (STAGING_PREFIX, ExtractError, Limits, clean_name, cleanup_staging, detect, extract,
                              extract_tar_stream)


# ---------- builders ----------

def make_tgz(path: Path, members):
    """members: list of (TarInfo kwargs dict, bytes|None)."""
    with tarfile.open(path, "w:gz") as tf:
        for kw, data in members:
            ti = tarfile.TarInfo(kw.pop("name"))
            for k, v in kw.items():
                setattr(ti, k, v)
            if data is not None:
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
            else:
                tf.addfile(ti)
    return path


def reg(name, data=b"x", mode=0o644):
    return ({"name": name, "mode": mode}, data)


def make_zip(path: Path, files: dict, compression=zipfile.ZIP_DEFLATED):
    with zipfile.ZipFile(path, "w", compression) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


# Minimal ZipCrypto writer (the stdlib can only read it), stored entries.
_T = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ 0xEDB88320 if _c & 1 else _c >> 1
    _T.append(_c)


def _crc(c, b):
    return (c >> 8) ^ _T[(c ^ b) & 0xFF]


def _zipcrypto(data: bytes, pwd: bytes, check: int) -> bytes:
    k = [0x12345678, 0x23456789, 0x34567890]

    def upd(b):
        k[0] = _crc(k[0], b)
        k[1] = (k[1] + (k[0] & 0xFF)) * 134775813 + 1 & 0xFFFFFFFF
        k[2] = _crc(k[2], k[1] >> 24)

    for b in pwd:
        upd(b)
    out = bytearray()
    for p in bytes(11) + bytes([check]) + data:
        t = k[2] | 2
        out.append(p ^ (((t * (t ^ 1)) >> 8) & 0xFF))
        upd(p)
    return bytes(out)


def raw_zip(path: Path, entries, pwd: bytes | None = None, method=0):
    """entries: list of (name, data, unix_mode). Hand-built so we can set any flag/method/mode."""
    local, central = bytearray(), bytearray()
    for name, data, mode in entries:
        n = name.encode()
        crc = zlib.crc32(data)
        flags = 1 if pwd else 0
        body = _zipcrypto(data, pwd, crc >> 24) if pwd else data
        off = len(local)
        local += struct.pack("<4sHHHHHIIIHH", b"PK\x03\x04", 20, flags, method, 0, 33, crc, len(body), len(data), len(n), 0) + n + body
        central += struct.pack("<4sHHHHHHIIIHHHHHII", b"PK\x01\x02", 0x0314, 20, flags, method, 0, 33, crc,
                               len(body), len(data), len(n), 0, 0, 0, 0, mode << 16, off) + n
    eocd = struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, len(entries), len(entries), len(central), len(local), 0)
    path.write_bytes(bytes(local + central + eocd))
    return path


def left_clean(tmp_path: Path, dest: Path):
    assert not dest.exists()
    assert not [p for p in dest.parent.iterdir() if p.name.startswith(STAGING_PREFIX)]


def fails(code, archive, tmp_path, **kw):
    dest = tmp_path / "out" / "src"
    with pytest.raises(ExtractError) as ei:
        extract(archive, dest, **kw)
    assert ei.value.code == code, ei.value
    if dest.parent.exists():
        left_clean(tmp_path, dest)


# ---------- good archives ----------

def test_tgz_roundtrip_modes_and_dot_prefix(tmp_path):
    a = make_tgz(tmp_path / "a.tgz", [
        ({"name": ".", "type": tarfile.DIRTYPE, "mode": 0o755}, None),
        reg("./Makefile", b"all:\n"),
        reg("./bin/run.sh", b"#!/bin/sh\n", 0o4755),     # setuid must vanish, exec stays
        reg("./secret.txt", b"s", 0o600),
        ({"name": "./empty", "type": tarfile.DIRTYPE, "mode": 0o777}, None),
    ])
    dest = tmp_path / "out" / "src"
    rep = extract(a, dest)
    assert (dest / "Makefile").read_bytes() == b"all:\n"
    assert stat.S_IMODE((dest / "bin/run.sh").stat().st_mode) == 0o755
    assert stat.S_IMODE((dest / "secret.txt").stat().st_mode) == 0o644
    assert stat.S_IMODE((dest / "empty").stat().st_mode) == 0o755
    assert rep.kind == "tgz" and rep.files == 3 and rep.bytes == len(b"all:\n#!/bin/sh\ns")
    left_clean(tmp_path, tmp_path / "out" / "nothing")


def test_zip_strip_single_root(tmp_path):
    a = make_zip(tmp_path / "a.zip", {"proj-main/": b"", "proj-main/a.c": b"int x;", "proj-main/d/b.h": b"//"})
    dest = tmp_path / "out" / "src"
    rep = extract(a, dest, strip_root=True)
    assert rep.stripped == "proj-main"
    assert (dest / "a.c").read_bytes() == b"int x;" and (dest / "d/b.h").exists()


def test_no_strip_when_several_tops(tmp_path):
    a = make_zip(tmp_path / "a.zip", {"a/x": b"1", "b/y": b"2"})
    rep = extract(a, tmp_path / "src", strip_root=True)
    assert rep.stripped is None and (tmp_path / "src/a/x").exists()


def test_nested_archive_is_just_a_file(tmp_path):
    inner = make_zip(tmp_path / "inner.zip", {"evil/../../x": b"!"})
    a = make_zip(tmp_path / "a.zip", {"vendor/inner.zip": inner.read_bytes()})
    dest = tmp_path / "src"
    extract(a, dest)
    assert (dest / "vendor/inner.zip").read_bytes() == inner.read_bytes()
    assert not (tmp_path / "x").exists()


def test_zipcrypto_with_right_password(tmp_path):
    a = raw_zip(tmp_path / "a.zip", [("main.py", b"print(1)\n", 0o100644)], pwd=b"s3cret-pass")
    dest = tmp_path / "src"
    extract(a, dest, password=b"s3cret-pass")
    assert (dest / "main.py").read_bytes() == b"print(1)\n"


def test_password_ignored_for_plain_zip(tmp_path):
    a = make_zip(tmp_path / "a.zip", {"a": b"1"})
    extract(a, tmp_path / "src", password=b"whatever")
    assert (tmp_path / "src/a").exists()


# ---------- refusals ----------

@pytest.mark.parametrize("name", ["/etc/passwd", "../x", "a/../../x", "a/..", "C:/x", "c:x", "a\\..\\x",
                                  "a/\x1bb", "x" * 300])
def test_bad_names_tar(tmp_path, name):
    fails("bad_name", make_tgz(tmp_path / "a.tgz", [reg("ok"), reg(name)]), tmp_path)


def test_nul_in_name():
    # tar headers cut names at NUL, so it can only arrive through other paths: check the rule itself
    with pytest.raises(ExtractError):
        clean_name("a\x00b", Limits())


@pytest.mark.parametrize("name", ["/abs", "../up", "C:/win", "a\\b"])
def test_bad_names_zip(tmp_path, name):
    fails("bad_name", raw_zip(tmp_path / "a.zip", [("ok", b"1", 0o100644), (name, b"2", 0o100644)]), tmp_path)


def test_deep_path_refused(tmp_path):
    fails("bad_name", make_tgz(tmp_path / "a.tgz", [reg("/".join(["d"] * 70))]), tmp_path)


@pytest.mark.parametrize("kind, code", [(tarfile.SYMTYPE, "link"), (tarfile.LNKTYPE, "link"),
                                        (tarfile.CHRTYPE, "special"), (tarfile.BLKTYPE, "special"),
                                        (tarfile.FIFOTYPE, "special")])
def test_tar_special_members(tmp_path, kind, code):
    a = make_tgz(tmp_path / "a.tgz", [reg("ok"), ({"name": "x", "type": kind, "linkname": "/etc/passwd"}, None)])
    fails(code, a, tmp_path)


def test_zip_symlink_refused(tmp_path):
    fails("link", raw_zip(tmp_path / "a.zip", [("l", b"/etc/passwd", 0o120777)]), tmp_path)


def test_zip_fifo_refused(tmp_path):
    fails("special", raw_zip(tmp_path / "a.zip", [("f", b"", 0o010644)]), tmp_path)


def test_duplicates_and_clashes(tmp_path):
    fails("duplicate", make_tgz(tmp_path / "a.tgz", [reg("a"), reg("a")]), tmp_path)
    fails("duplicate", make_tgz(tmp_path / "b.tgz", [reg("a"), reg("a/b")]), tmp_path)
    fails("duplicate", make_tgz(tmp_path / "c.tgz", [reg("a/b"), reg("a")]), tmp_path)
    fails("duplicate", make_tgz(tmp_path / "d.tgz", [reg("./a"), reg("a")]), tmp_path)


def test_limits(tmp_path):
    many = make_tgz(tmp_path / "m.tgz", [reg(f"f{i}") for i in range(11)])
    fails("too_many_entries", many, tmp_path, limits=Limits(max_entries=10))
    big = make_tgz(tmp_path / "b.tgz", [reg("big", b"a" * 5000)])
    fails("too_large", big, tmp_path, limits=Limits(max_file=4096))
    tot = make_tgz(tmp_path / "t.tgz", [reg("a", b"a" * 3000), reg("b", b"b" * 3000)])
    fails("too_large", tot, tmp_path, limits=Limits(max_total=5000))


def test_zip_bomb_ratio(tmp_path):
    a = make_zip(tmp_path / "bomb.zip", {"zeros": b"\x00" * (4 * 1024 * 1024)})
    fails("ratio", a, tmp_path, limits=Limits(ratio_floor=1024 * 1024))


def test_tgz_bomb_ratio(tmp_path):
    a = make_tgz(tmp_path / "bomb.tgz", [reg("zeros", b"\x00" * (4 * 1024 * 1024))])
    fails("ratio", a, tmp_path, limits=Limits(ratio_floor=1024 * 1024))


def test_not_archive_by_magic(tmp_path):
    fake = tmp_path / "code.zip"
    fake.write_bytes(b"#!/bin/sh\necho hi\n")
    fails("not_archive", fake, tmp_path)
    assert detect(make_zip(tmp_path / "renamed.tgz", {"a": b"1"})) == "zip"


def test_corrupt_gzip(tmp_path):
    bad = tmp_path / "a.tgz"
    bad.write_bytes(b"\x1f\x8b\x08\x00garbage-not-gzip")
    fails("corrupt", bad, tmp_path)


def test_encrypted_without_password(tmp_path):
    fails("password_required", raw_zip(tmp_path / "a.zip", [("a", b"1", 0o100644)], pwd=b"pw"), tmp_path)


@pytest.mark.parametrize("wrong", [b"nope", b"s3cret-pasS", b"", b"x" * 40, b"123456"])
def test_wrong_password(tmp_path, wrong):
    a = raw_zip(tmp_path / "a.zip", [("a.py", b"print('hello world')\n" * 20, 0o100644)], pwd=b"s3cret-pass")
    fails("password_required" if not wrong else "bad_password", a, tmp_path, password=wrong)


def test_zip_aes_refused_with_clear_code(tmp_path):
    fails("zip_aes", raw_zip(tmp_path / "a.zip", [("a", b"1", 0o100644)], method=99), tmp_path)


def test_dest_must_not_exist(tmp_path):
    a = make_zip(tmp_path / "a.zip", {"a": b"1"})
    (tmp_path / "src").mkdir()
    with pytest.raises(ExtractError) as ei:
        extract(a, tmp_path / "src")
    assert ei.value.code == "dest_exists"
    assert list((tmp_path / "src").iterdir()) == []


def test_failure_midway_leaves_nothing(tmp_path):
    """Good entries are written in pass 2, then a later entry blows the cap: all rolled back."""
    a = make_tgz(tmp_path / "a.tgz", [reg("a", b"1"), reg("b", b"2"), reg("c", b"x" * 5000)])
    dest = tmp_path / "out" / "src"
    with pytest.raises(ExtractError):
        extract(a, dest, limits=Limits(max_total=4000))
    left_clean(tmp_path, dest)


def test_allowed_roots(tmp_path):
    a = make_tgz(tmp_path / "a.tgz", [reg("workspace/src/a"), reg("web/marks.json"), reg("etc/cron.d/x")])
    fails("root_not_allowed", a, tmp_path, roots=("workspace", "web"))
    ok = make_tgz(tmp_path / "ok.tgz", [reg("workspace/src/a"), reg("web/marks.json")])
    assert extract(ok, tmp_path / "r", roots=("workspace", "web")).roots == ["web", "workspace"]


# ---------- stream mode (rescue: gpg's stdout) ----------

class OneWay(io.RawIOBase):
    """Non-seekable reader, like a pipe."""

    def __init__(self, data):
        self.b = io.BytesIO(data)

    def readable(self):
        return True

    def readinto(self, buf):
        d = self.b.read(len(buf))
        buf[:len(d)] = d
        return len(d)


def test_stream_roundtrip_and_refusal(tmp_path):
    good = make_tgz(tmp_path / "g.tgz", [reg("workspace/src/a.c", b"int a;"), reg("web/jobs.json", b"[]")])
    dest = tmp_path / "rescue"
    rep = extract_tar_stream(OneWay(good.read_bytes()), dest, roots=("workspace", "web"))
    assert (dest / "workspace/src/a.c").read_bytes() == b"int a;" and rep.files == 2

    bad = make_tgz(tmp_path / "b.tgz", [reg("workspace/a"), ({"name": "workspace/l", "type": tarfile.SYMTYPE,
                                                              "linkname": "/"}, None)])
    dest2 = tmp_path / "rescue2"
    with pytest.raises(ExtractError) as ei:
        extract_tar_stream(OneWay(bad.read_bytes()), dest2)
    assert ei.value.code == "link"
    left_clean(tmp_path, dest2)


def test_stream_truncated_is_corrupt(tmp_path):
    good = make_tgz(tmp_path / "g.tgz", [reg("a", os.urandom(200_000))]).read_bytes()
    with pytest.raises(ExtractError) as ei:
        extract_tar_stream(OneWay(good[: len(good) // 2]), tmp_path / "r")
    assert ei.value.code == "corrupt"
    left_clean(tmp_path, tmp_path / "r")


def test_cleanup_staging(tmp_path):
    (tmp_path / (STAGING_PREFIX + "abc") / "x").mkdir(parents=True)
    (tmp_path / "keep").mkdir()
    assert cleanup_staging(tmp_path) == 1
    assert [p.name for p in tmp_path.iterdir()] == ["keep"]
    assert cleanup_staging(tmp_path / "missing") == 0
