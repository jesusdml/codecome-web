# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Worker-side maintenance handlers (v1.1): upload now; archive and rescue in the next steps.

Each handler is `handler(job, secret, log) -> dict`; it raises MaintError / ExtractError for clean
failures (see Runner.run_maintenance). They only ever touch CODECOME_ROOT/src (and, later, the
workspace outputs and the archive store), never anything named by the browser except a validated id.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import uploads
from .project_cfg import ProjectNameError, clean_name, set_name
from .gates import PLACEHOLDERS, src_has_code
from .runner import MaintError
from .safe_extract import ExtractError, extract

UPLOAD_ERRORS_KEEP = ("bad_password", "password_required")  # the user can retry with the same file


def make_handlers(root: Path, data: Path, *, store: Path | None = None, db=None, cost=None) -> dict:
    """`store` = archive directory, `db` = JobDB, `cost(project, since)` = USD from the ledger."""
    root, data = Path(root), Path(data)

    def upload(job: dict, secret: bytearray | None, log) -> dict:
        try:
            part = uploads.path_of(data / "uploads", job.get("target"))
        except uploads.UploadError:
            raise MaintError("upload_not_found")
        if not part.is_file() or part.is_symlink():
            raise MaintError("upload_not_found")
        try:  # the new audit's name: checked before anything is touched, written once the code is in
            project = clean_name((job.get("params") or {}).get("project"))
        except ProjectNameError:
            raise MaintError("project_name_invalid")
        src = root / "src"
        if src.is_symlink() or src_has_code(root) or (src.exists() and not src.is_dir()):
            raise MaintError("src_not_empty")
        log(f"archive received: {part.stat().st_size} bytes")
        removed_dir = False
        if src.is_dir():  # empty (or only placeholders): make room for the atomic rename
            for keep in PLACEHOLDERS:
                try:
                    os.unlink(src / keep)
                except FileNotFoundError:
                    pass
            os.rmdir(src)
            removed_dir = True
        try:
            # zipfile only takes bytes: this copy cannot be wiped; it lives for this call only.
            rep = extract(part, src, password=bytes(secret) if secret else None, strip_root=True)
        except ExtractError as e:
            if removed_dir and not src.exists():
                src.mkdir(mode=0o755)
            if e.code not in UPLOAD_ERRORS_KEEP:
                part.unlink(missing_ok=True)
            raise
        part.unlink(missing_ok=True)
        try:
            set_name(root / "codecome.yml", project)
        except (ProjectNameError, OSError) as e:
            log(f"codecome.yml: {type(e).__name__} {getattr(e, 'code', '')}")
            raise MaintError("project_name_write_failed") from e
        log(f"project name: {project}")
        log(f"src/ ready: {rep.files} files, {rep.dirs} dirs, {rep.bytes} bytes"
            + (f" (top folder {rep.stripped}/ removed)" if rep.stripped else ""))
        return {"kind": rep.kind, "files": rep.files, "dirs": rep.dirs, "bytes": rep.bytes, "stripped": rep.stripped,
                "project": project}

    handlers = {"upload": upload}
    if store is not None and db is not None:
        from .archive import make_archive_handler, make_rescue_handler
        handlers["archive"] = make_archive_handler(root, data, store, db, cost)
        handlers["rescue"] = make_rescue_handler(root, store, db)
    if db is not None:
        from .archive import make_reset_handler
        handlers["reset"] = make_reset_handler(root, db)
    return handlers
