# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""One-shot secrets from the web to the worker (archive / rescue / zip passwords).

The web writes the secret to `<data>/secrets/<32 hex>` (0640, group shared with the worker; the
directory is 2770) and puts only the NAME in the job row. The worker reads it once and unlinks
it. Never in argv, environment, the database (WAL pages keep deleted rows) or logs.
Files older than an hour are swept when the worker starts (abandoned jobs).
"""
from __future__ import annotations

import os
import re
import secrets
import stat
import time
from pathlib import Path

NAME_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_SECRET = 4096
MAX_AGE_S = 3600


class SecretError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _dir(base: Path) -> Path:
    d = Path(base)
    d.mkdir(mode=0o2770, parents=True, exist_ok=True)
    return d


def put(base: Path, data: bytes) -> str:
    if not data or len(data) > MAX_SECRET:
        raise SecretError("secret_invalid")
    name = secrets.token_hex(16)
    fd = os.open(_dir(base) / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    try:
        os.fchmod(fd, 0o640)  # whatever the umask says
        os.write(fd, data)
    finally:
        os.close(fd)
    return name


def take(base: Path, name: str) -> bytearray:
    """Read and delete. The caller zeroes the returned buffer when done (`wipe`)."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise SecretError("secret_invalid")
    path = Path(base) / name
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise SecretError("secret_missing")
    except OSError:
        raise SecretError("secret_invalid")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_SECRET:
            raise SecretError("secret_invalid")
        buf = bytearray(st.st_size)
        n = os.readv(fd, [buf])
        del buf[n:]
    finally:
        os.close(fd)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    return buf


def wipe(buf: bytearray | None) -> None:
    if buf is not None:
        buf[:] = b"\x00" * len(buf)


def discard(base: Path, name: str | None) -> None:
    if name and NAME_RE.match(name):
        try:
            os.unlink(Path(base) / name)
        except FileNotFoundError:
            pass


def sweep(base: Path, max_age: float = MAX_AGE_S, now: float | None = None) -> int:
    now = now or time.time()
    n = 0
    try:
        names = os.listdir(base)
    except FileNotFoundError:
        return 0
    for name in names:
        p = Path(base) / name
        try:
            st = os.lstat(p)
            if NAME_RE.match(name) and stat.S_ISREG(st.st_mode) and now - st.st_mtime > max_age:
                os.unlink(p)
                n += 1
        except FileNotFoundError:
            pass
    return n
