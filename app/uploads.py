# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Web side of "Upload code": the browser PUTs the raw archive, we stream it to
`<data>/uploads/<32 hex>.part` (0640, group shared with the worker) with a hard size cap and a
magic-byte check. Extraction is the worker's job (`app/maintenance.py`, `app/safe_extract.py`).
"""
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import AsyncIterator

ID_RE = re.compile(r"^[0-9a-f]{32}$")
SUFFIX = ".part"
MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"\x1f\x8b")
MAX_AGE_S = 24 * 3600


class UploadError(Exception):
    def __init__(self, code: str, status: int = 400, **params):
        super().__init__(code)
        self.code, self.status, self.params = code, status, params


def path_of(base: Path, upload_id: str) -> Path:
    if not isinstance(upload_id, str) or not ID_RE.match(upload_id):
        raise UploadError("upload_not_found", 404)
    return Path(base) / (upload_id + SUFFIX)


def clear(base: Path, keep: str | None = None, max_age: float | None = None, now: float | None = None) -> int:
    """Remove pending uploads (all but `keep`, or only those older than `max_age`)."""
    import time
    now = now or time.time()
    n = 0
    try:
        names = os.listdir(base)
    except FileNotFoundError:
        return 0
    for name in names:
        if not name.endswith(SUFFIX) or not ID_RE.match(name[: -len(SUFFIX)]) or name[: -len(SUFFIX)] == keep:
            continue
        p = Path(base) / name
        try:
            if max_age is not None and now - os.lstat(p).st_mtime <= max_age:
                continue
            os.unlink(p)
            n += 1
        except FileNotFoundError:
            pass
    return n


async def receive(chunks: AsyncIterator[bytes], base: Path, max_bytes: int) -> tuple[str, int]:
    """Write the body to a new upload file; returns (id, size). Deleted again on any refusal."""
    base = Path(base)
    base.mkdir(mode=0o2770, parents=True, exist_ok=True)
    upload_id = secrets.token_hex(16)
    path = base / (upload_id + SUFFIX)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    size, head, ok = 0, b"", False
    try:
        os.fchmod(fd, 0o640)
        async for chunk in chunks:
            if not chunk:
                continue
            size += len(chunk)
            if size > max_bytes:
                raise UploadError("upload_too_large", 413, max_mb=max_bytes // (1024 * 1024))
            if len(head) < 4:
                head = (head + chunk)[:4]
            os.write(fd, chunk)
        if size == 0:
            raise UploadError("upload_empty")
        if not head.startswith(MAGIC):  # the type by content, never by the file name
            raise UploadError("not_archive")
        ok = True
        return upload_id, size
    finally:
        os.close(fd)
        if not ok:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
