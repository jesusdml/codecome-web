# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Web side of the Archive screen: reads the unencrypted `<id>.json` cards written by the worker
(app/archive.py) and serves the encrypted file. Read only; the web never writes the store.

The cards are written by our own worker, but they are still files on disk: only known fields
with checked types leave this module, and names must match the id pattern (no paths).
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}-\d{8}-\d{6}$")
CARD_MAX = 64 * 1024
MAX_LIST = 500
STATUSES = ("PENDING", "CONFIRMED", "EXPLOITED", "REJECTED", "DUPLICATE")
PHASE_STATUSES = ("none", "queued", "running", "done", "failed", "partial")


class StoreError(Exception):
    def __init__(self, code: str, status: int = 404):
        super().__init__(code)
        self.code, self.status = code, status


def _num(v, kind=int):
    return v if isinstance(v, kind) and not isinstance(v, bool) and v >= 0 else None


def _str(v, n=200):
    return v[:n] if isinstance(v, str) else None


def _card(raw: dict, aid: str) -> dict:
    phases = raw.get("phases") if isinstance(raw.get("phases"), dict) else {}
    findings = raw.get("findings") if isinstance(raw.get("findings"), dict) else {}
    digest = raw.get("sha256") if isinstance(raw.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", raw["sha256"]) else None
    cost = raw.get("cost_usd")
    return {
        "id": aid,
        "project": _str(raw.get("project"), 80),
        "created_at": _num(raw.get("created_at"), (int, float)),
        "by": _str(raw.get("by"), 64),
        "size": _num(raw.get("size")),
        "sha256": digest,
        "files": _num(raw.get("files")),
        "bytes": _num(raw.get("bytes")),
        "jobs": _num(raw.get("jobs")),
        "assumed": _num(raw.get("assumed")),
        "cost_usd": round(cost, 4) if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0 else None,
        "phases": {k: v for k, v in phases.items() if k in ("1", "2", "3", "6") and v in PHASE_STATUSES},
        "models": [m for m in (raw.get("models") if isinstance(raw.get("models"), list) else [])
                   if isinstance(m, str) and re.fullmatch(r"[\w.:/@+-]{1,60}", m)][:6],
        "findings": {k: v for k, v in findings.items() if k in STATUSES and _num(v) is not None},
    }


def _read_card(path: Path) -> dict | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > CARD_MAX:
            return None
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = json.loads(f.read(CARD_MAX + 1).decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    finally:
        os.close(fd)


def list_archives(store: Path) -> list[dict]:
    """Newest first. Cards without their encrypted file (or broken) are left out."""
    store = Path(store)
    try:
        names = os.listdir(store)
    except OSError:
        return []
    out = []
    for name in names:
        if not name.endswith(".json") or not ID_RE.match(name[:-5]):
            continue
        aid = name[:-5]
        raw = _read_card(store / name)
        enc = store / f"{aid}.tar.gz.gpg"
        if raw is None or raw.get("id") != aid or enc.is_symlink() or not enc.is_file():
            continue
        out.append(_card(raw, aid))
    out.sort(key=lambda c: (c["created_at"] or 0, c["id"]), reverse=True)
    return out[:MAX_LIST]


def encrypted_file(store: Path, aid: str) -> Path:
    if not isinstance(aid, str) or not ID_RE.match(aid):
        raise StoreError("archive_not_found")
    p = Path(store) / f"{aid}.tar.gz.gpg"
    try:
        st = os.lstat(p)
    except OSError:
        raise StoreError("archive_not_found")
    if not stat.S_ISREG(st.st_mode):
        raise StoreError("archive_not_found")
    return p
