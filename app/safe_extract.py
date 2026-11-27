# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Hardened extraction of untrusted archives (.tar.gz / .tgz / .zip, optionally ZipCrypto).

Used by "Upload code" and by "Rescue" (which feeds a decrypted tar stream). Never shells out to
`tar -x` / `unzip`: every entry is checked and written by this code.

Rules:
- the type comes from the magic bytes, never from the file name; nested archives are just files;
- names: relative, `/`-separated, no `..`, no drive letters, no backslashes, no NUL, bounded
  length and depth; a leading `./` is allowed; duplicates and file/dir clashes are rejected;
- only regular files and directories: symlinks, hard links, devices and FIFOs are rejected;
- modes are ours (0644, or 0755 if any exec bit was set; dirs 0755), never setuid/setgid/sticky;
  the owner is whoever runs this (the worker);
- caps on entry count, file size, total size and compression ratio (zip bombs), enforced on the
  bytes actually written, not only on what the headers claim;
- everything goes into a fresh staging dir next to `dest` and is renamed into place at the end:
  all or nothing. On any error the staging dir is removed and `dest` is untouched.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import tarfile
import tempfile
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Iterable

STAGING_PREFIX = ".ccw-staging-"
CHUNK = 1 << 16
ZIP_AES = 99  # WinZip AES "compression" method: not supported by the stdlib


class ExtractError(Exception):
    """`code` is a stable key for the UI (i18n); `detail` is for logs, never shown as markup."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Limits:
    max_entries: int = 100_000
    max_file: int = 512 * 1024 * 1024          # one file, uncompressed
    max_total: int = 2 * 1024 * 1024 * 1024    # all files, uncompressed
    max_ratio: int = 100                       # uncompressed / compressed
    ratio_floor: int = 16 * 1024 * 1024        # below this many bytes the ratio is not checked
    max_name: int = 1024
    max_component: int = 255
    max_depth: int = 64


@dataclass
class Report:
    kind: str
    files: int = 0
    dirs: int = 0
    bytes: int = 0
    stripped: str | None = None
    roots: list[str] = field(default_factory=list)


def detect(path: Path) -> str:
    """'zip' or 'tgz' by magic bytes; anything else is refused."""
    with open(path, "rb") as f:
        head = f.read(4)
    if head in (b"PK\x03\x04", b"PK\x05\x06"):
        return "zip"
    if head[:2] == b"\x1f\x8b":
        return "tgz"
    raise ExtractError("not_archive")


_DRIVE = re.compile(r"^[A-Za-z]:")


def clean_name(raw: str, limits: Limits) -> tuple[str, ...]:
    """Archive entry name -> tuple of path components, or ExtractError('bad_name')."""
    if not raw or "\x00" in raw or "\\" in raw or len(raw.encode("utf-8", "surrogateescape")) > limits.max_name:
        raise ExtractError("bad_name", repr(raw[:200]))
    if raw.startswith("/") or _DRIVE.match(raw):
        raise ExtractError("bad_name", repr(raw[:200]))
    name = raw
    while name.startswith("./"):
        name = name[2:]
    parts = tuple(p for p in name.split("/") if p not in ("", "."))
    if not parts or len(parts) > limits.max_depth:
        raise ExtractError("bad_name", repr(raw[:200]))
    for p in parts:
        if p == ".." or len(p.encode("utf-8", "surrogateescape")) > limits.max_component or any(ord(c) < 32 for c in p):
            raise ExtractError("bad_name", repr(raw[:200]))
    return parts


@dataclass
class _Entry:
    parts: tuple[str, ...]
    is_dir: bool
    exec_bit: bool
    size: int          # declared uncompressed size (files)
    ref: object        # TarInfo / ZipInfo


class _Plan:
    """Validates entries and catches duplicates and file/dir clashes before anything is written."""

    def __init__(self, limits: Limits, roots: Iterable[str] | None):
        self.limits = limits
        self.roots = set(roots) if roots is not None else None
        self.files: set[tuple[str, ...]] = set()
        self.dirs: set[tuple[str, ...]] = set()
        self.count = 0
        self.declared = 0

    def add(self, e: _Entry) -> None:
        lim = self.limits
        self.count += 1
        if self.count > lim.max_entries:
            raise ExtractError("too_many_entries")
        if self.roots is not None and e.parts[0] not in self.roots:
            raise ExtractError("root_not_allowed", e.parts[0][:100])
        for i in range(1, len(e.parts)):  # every parent is (implicitly) a directory
            parent = e.parts[:i]
            if parent in self.files:
                raise ExtractError("duplicate", "/".join(parent))
            self.dirs.add(parent)
        if e.is_dir:
            if e.parts in self.files:
                raise ExtractError("duplicate", "/".join(e.parts))
            self.dirs.add(e.parts)
            return
        if e.parts in self.files or e.parts in self.dirs:
            raise ExtractError("duplicate", "/".join(e.parts))
        if e.size > lim.max_file:
            raise ExtractError("too_large", "/".join(e.parts))
        self.declared += e.size
        if self.declared > lim.max_total:
            raise ExtractError("too_large")
        self.files.add(e.parts)


class _Writer:
    """Writes into the staging dir, counting the bytes really produced."""

    def __init__(self, staging: Path, limits: Limits, compressed: int | None):
        self.root = staging
        self.limits = limits
        self.compressed = compressed
        self.report_bytes = 0
        self.files = 0
        self.dirs = 0

    def _check_ratio(self) -> None:
        lim = self.limits
        if self.compressed is not None and self.report_bytes > lim.ratio_floor \
                and self.report_bytes > self.compressed * lim.max_ratio:
            raise ExtractError("ratio")

    def mkdir(self, parts: tuple[str, ...]) -> None:
        p = self.root
        for c in parts:
            p = p / c
            try:
                os.mkdir(p, 0o755)
                self.dirs += 1
            except FileExistsError:
                if not stat.S_ISDIR(os.lstat(p).st_mode):
                    raise ExtractError("duplicate", str(p.relative_to(self.root)))

    def write(self, parts: tuple[str, ...], src: BinaryIO, exec_bit: bool) -> None:
        if len(parts) > 1:
            self.mkdir(parts[:-1])
        path = self.root.joinpath(*parts)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        written = 0
        try:
            while True:
                b = src.read(CHUNK)
                if not b:
                    break
                written += len(b)
                self.report_bytes += len(b)
                if written > self.limits.max_file or self.report_bytes > self.limits.max_total:
                    raise ExtractError("too_large", "/".join(parts))
                self._check_ratio()
                os.write(fd, b)
            os.fchmod(fd, 0o755 if exec_bit else 0o644)
        finally:
            os.close(fd)
        self.files += 1


def _tar_entry(m: tarfile.TarInfo, limits: Limits) -> _Entry | None:
    if m.isdir() and m.name.rstrip("/") in (".", ""):
        return None  # the "./" entry of `tar czf x.tgz .`
    parts = clean_name(m.name, limits)
    if m.issym() or m.islnk():
        raise ExtractError("link", m.name[:200])
    if m.isdir():
        return _Entry(parts, True, False, 0, m)
    if not m.isreg():
        raise ExtractError("special", m.name[:200])  # devices, FIFOs, anything else
    return _Entry(parts, False, bool(m.mode & 0o111), m.size, m)


def _zip_entry(i: zipfile.ZipInfo, limits: Limits) -> _Entry:
    parts = clean_name(i.filename, limits)
    mode = i.external_attr >> 16
    fmt = stat.S_IFMT(mode)
    if fmt == stat.S_IFLNK:
        raise ExtractError("link", i.filename[:200])
    if fmt not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise ExtractError("special", i.filename[:200])
    if i.compress_type == ZIP_AES:
        raise ExtractError("zip_aes")
    if i.is_dir() or fmt == stat.S_IFDIR:
        return _Entry(parts, True, False, 0, i)
    if i.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
        raise ExtractError("unsupported_method", str(i.compress_type))
    return _Entry(parts, False, bool(mode & 0o111), i.file_size, i)


def _strip(entries: list[_Entry]) -> str | None:
    """If everything lives under one top directory (project-main/...), its name; else None."""
    tops = {e.parts[0] for e in entries}
    if len(tops) != 1:
        return None
    top = next(iter(tops))
    if any(len(e.parts) == 1 and not e.is_dir for e in entries):
        return None  # a single top-level *file*, nothing to strip
    if not any(len(e.parts) > 1 for e in entries):
        return None
    return top


class _Staging:
    def __init__(self, dest: Path):
        self.dest = dest
        if os.path.lexists(dest):
            raise ExtractError("dest_exists", str(dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.path = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=dest.parent))
        os.chmod(self.path, 0o755)

    def commit(self) -> None:
        if os.path.lexists(self.dest):
            raise ExtractError("dest_exists", str(self.dest))
        os.rename(self.path, self.dest)

    def discard(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def extract(archive: Path, dest: Path, *, password: bytes | None = None, limits: Limits = Limits(),
            strip_root: bool = False, roots: Iterable[str] | None = None) -> Report:
    """Extract the archive FILE into `dest` (which must not exist yet). Two passes: validate
    every entry first, then write. Raises ExtractError; on error nothing is left behind."""
    kind = detect(archive)
    compressed = os.path.getsize(archive)
    stg = _Staging(dest)
    try:
        if kind == "zip":
            rep = _extract_zip(archive, stg.path, password, limits, strip_root, roots, compressed)
        else:
            rep = _extract_tgz(archive, stg.path, limits, strip_root, roots, compressed)
        stg.commit()
        return rep
    except BaseException:
        stg.discard()
        raise


def extract_tar_stream(stream: BinaryIO, dest: Path, *, limits: Limits = Limits(),
                       roots: Iterable[str] | None = None) -> Report:
    """Extract a gzip'ed tar read from a non-seekable stream (e.g. gpg's stdout) in ONE pass:
    each entry is validated right before it is written. Same all-or-nothing guarantee."""
    stg = _Staging(dest)
    try:
        plan = _Plan(limits, roots)
        w = _Writer(stg.path, limits, None)
        try:
            with tarfile.open(fileobj=stream, mode="r|gz") as tf:
                for m in tf:
                    e = _tar_entry(m, limits)
                    if e is None:
                        continue
                    plan.add(e)
                    if e.is_dir:
                        w.mkdir(e.parts)
                    else:
                        f = tf.extractfile(m)
                        w.write(e.parts, f, e.exec_bit)
        except (tarfile.TarError, EOFError, zlib.error, OSError) as ex:
            raise ExtractError("corrupt", type(ex).__name__)
        stg.commit()
        return Report("tgz", w.files, w.dirs, w.report_bytes, None, sorted({p[0] for p in plan.files | plan.dirs}))
    except BaseException:
        stg.discard()
        raise


def _extract_tgz(archive, staging, limits, strip_root, roots, compressed) -> Report:
    try:
        with tarfile.open(archive, mode="r:gz") as tf:
            entries = []
            for m in tf:  # pass 1: headers only
                e = _tar_entry(m, limits)
                if e is not None:
                    entries.append(e)
                if len(entries) > limits.max_entries:
                    raise ExtractError("too_many_entries")
            return _write_all(entries, staging, limits, strip_root, roots, compressed, "tgz",
                              lambda e: tf.extractfile(e.ref))
    except (tarfile.TarError, EOFError, zlib.error) as ex:
        raise ExtractError("corrupt", type(ex).__name__)


def _extract_zip(archive, staging, password, limits, strip_root, roots, compressed) -> Report:
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            if len(infos) > limits.max_entries:
                raise ExtractError("too_many_entries")
            entries = [_zip_entry(i, limits) for i in infos]
            encrypted = any(e.ref.flag_bits & 0x1 for e in entries if not e.is_dir)
            if encrypted and not password:
                raise ExtractError("password_required")
            for e in entries:  # declared ratio per entry, before decompressing anything
                i = e.ref
                if not e.is_dir and i.file_size > limits.ratio_floor and i.file_size > max(i.compress_size, 1) * limits.max_ratio:
                    raise ExtractError("ratio", i.filename[:200])

            def opener(e):
                try:
                    return zf.open(e.ref, pwd=password if e.ref.flag_bits & 0x1 else None)
                except RuntimeError as ex:  # "Bad password for file"
                    raise ExtractError("bad_password") from ex

            try:
                return _write_all(entries, staging, limits, strip_root, roots, compressed, "zip", opener)
            except (zipfile.BadZipFile, zlib.error, EOFError) as ex:
                # ZipCrypto's 1-byte check lets ~1/256 wrong passwords through; then the CRC or the
                # inflater fails on the garbage.
                garbage = isinstance(ex, (zlib.error, EOFError)) or "CRC" in str(ex)
                raise ExtractError("bad_password" if encrypted and garbage else "corrupt", str(ex)[:200])
    except zipfile.BadZipFile as ex:
        raise ExtractError("corrupt", str(ex)[:200])
    except NotImplementedError as ex:
        raise ExtractError("unsupported_method", str(ex)[:200])


def _write_all(entries, staging, limits, strip_root, roots, compressed, kind, opener) -> Report:
    top = _strip(entries) if strip_root else None
    if top:
        entries = [e for e in entries if len(e.parts) > 1 or not e.is_dir]
        for e in entries:
            e.parts = e.parts[1:]
    plan = _Plan(limits, roots)
    for e in entries:
        plan.add(e)
    w = _Writer(staging, limits, compressed)
    for e in entries:  # pass 2: write
        if e.is_dir:
            w.mkdir(e.parts)
        else:
            with opener(e) as f:
                w.write(e.parts, f, e.exec_bit)
    return Report(kind, w.files, w.dirs, w.report_bytes, top, sorted({p[0] for p in plan.files | plan.dirs}))


def cleanup_staging(parent: Path) -> int:
    """Remove leftovers of an interrupted extraction (worker start-up). Returns how many."""
    n = 0
    try:
        names = os.listdir(parent)
    except FileNotFoundError:
        return 0
    for name in names:
        p = parent / name
        if name.startswith(STAGING_PREFIX) and stat.S_ISDIR(os.lstat(p).st_mode):
            shutil.rmtree(p, ignore_errors=True)
            n += 1
    return n
