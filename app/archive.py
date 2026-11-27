# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Archive & reset (v1.1, worker side): everything the audit produced goes into ONE encrypted file,
is verified by decrypting it again, and only then is the workspace emptied.

Layout of the archive (tar.gz, then `gpg --symmetric` AES-256):
  workspace/src/...  workspace/itemdb/...  workspace/runs/...  workspace/codecome.yml
  web/jobs.json      web/marks.json        web/logs/<job id>.log
  web/manifest.json  (last: sha256 and size of every other member)
Next to it, unencrypted, `<id>.json`: what the Archive screen shows without the password.

CodeCome's own skeleton (`.gitkeep` files, itemdb/README.md) is neither archived nor removed.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import tarfile
import time
from pathlib import Path
from typing import Callable

from . import audit, gpgbox
from .audit_view import project_name
from .jobs import ACTIONS, JobDB
from .project_cfg import ProjectNameError, read_name, set_name
from .runner import MaintError
from .safe_extract import STAGING_PREFIX, Limits, extract_tar_stream

FORMAT = 1
TOP_DIRS = ("src", "itemdb", "runs")
TOP_FILES = ("codecome.yml",)
PLACEHOLDERS = (".gitkeep", ".keep")
SKELETON = ("itemdb/README.md",)
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}-\d{8}-\d{6}$")
CHUNK = 1 << 16


def slug(name: str | None) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40].strip("-")
    return s or "codecome"


def _skeleton(rel: str) -> bool:
    return rel.rsplit("/", 1)[-1] in PLACEHOLDERS or rel in SKELETON


def collect(root: Path, log: Callable[[str], None]) -> list[tuple[str, Path]]:
    """Regular files to archive, as (path relative to root, absolute path). Symlinks and special
    files are skipped (and counted): agents write in itemdb/ and runs/, we never follow them out."""
    root = Path(root)
    out: list[tuple[str, Path]] = []
    skipped = 0
    for name in TOP_FILES:
        p = root / name
        if p.is_file() and not p.is_symlink():
            out.append((name, p))
    for top in TOP_DIRS:
        base = root / top
        if base.is_symlink() or not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            keep = []
            for d in sorted(dirnames):
                if os.path.islink(os.path.join(dirpath, d)):
                    skipped += 1
                else:
                    keep.append(d)
            dirnames[:] = keep
            for n in sorted(filenames):
                p = Path(dirpath) / n
                rel = p.relative_to(root).as_posix()
                if _skeleton(rel):
                    continue
                if not stat.S_ISREG(os.lstat(p).st_mode):
                    skipped += 1
                    continue
                out.append((rel, p))
    if skipped:
        log(f"skipped {skipped} symlinks or special files")
    return out


def has_content(root: Path) -> bool:
    """Anything besides CodeCome's skeleton in src/, itemdb/ or runs/? Stops at the first file."""
    root = Path(root)
    for top in TOP_DIRS:
        base = root / top
        if base.is_symlink():
            return True
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            for n in filenames + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]:
                if not _skeleton((Path(dirpath) / n).relative_to(root).as_posix()):
                    return True
    return False


def check_writable(root: Path) -> None:
    """Emptying must not stop halfway: every directory we will touch has to be writable first."""
    root = Path(root)
    if not os.access(root, os.W_OK | os.X_OK):
        raise MaintError("workspace_not_writable", path=".")
    for top in TOP_DIRS:
        base = root / top
        if base.is_symlink() or not base.is_dir():
            continue
        for dirpath, dirnames, _ in os.walk(base, followlinks=False):
            dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
            if not os.access(dirpath, os.W_OK | os.X_OK):
                raise MaintError("workspace_not_writable", path=Path(dirpath).relative_to(root).as_posix()[:200])


def empty_workspace(root: Path) -> int:
    """Delete everything but the skeleton; directories left empty go too (top dirs stay)."""
    root = Path(root)
    removed = 0  # codecome.yml is CodeCome's configuration: archived as a copy, never removed
    for top in TOP_DIRS:
        base = root / top
        if base.is_symlink() or not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base, topdown=False, followlinks=False):
            for n in filenames + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]:
                p = Path(dirpath) / n
                if _skeleton(p.relative_to(root).as_posix()) and not p.is_symlink():
                    continue
                os.unlink(p)
                removed += 1
            if Path(dirpath) != base:
                try:
                    os.rmdir(dirpath)  # only if now empty: skeleton dirs keep their .gitkeep
                except OSError:
                    pass
    return removed


class _Hashing:
    """File-like reader that hashes what tarfile copies (one read, no second pass)."""

    def __init__(self, f):
        self.f, self.h, self.n = f, hashlib.sha256(), 0

    def read(self, size=-1):
        b = self.f.read(size)
        self.h.update(b)
        self.n += len(b)
        return b


def _add_bytes(tf: tarfile.TarFile, name: str, data: bytes, manifest: dict) -> None:
    ti = tarfile.TarInfo(name)
    ti.size, ti.mode, ti.mtime = len(data), 0o644, int(time.time())
    tf.addfile(ti, io.BytesIO(data))
    manifest[name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def _add_file(tf: tarfile.TarFile, name: str, path: Path, manifest: dict) -> int:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise MaintError("archive_changed", path=name[:200])
        ti = tarfile.TarInfo(name)
        ti.size, ti.mtime = st.st_size, int(st.st_mtime)
        ti.mode = 0o755 if st.st_mode & 0o111 else 0o644
        h = _Hashing(f)
        tf.addfile(ti, h)
        manifest[name] = {"sha256": h.h.hexdigest(), "size": h.n}
        return h.n


def verify(path: Path, passphrase, manifest: dict) -> None:
    """Decrypt and re-hash every member: the workspace is emptied only if this passes."""
    seen: dict[str, dict] = {}
    with gpgbox.decrypt_from(path, passphrase) as r:
        with tarfile.open(fileobj=r, mode="r|gz") as tf:
            for m in tf:
                if not m.isreg():
                    raise MaintError("archive_verify_failed", path=m.name[:200])
                f = tf.extractfile(m)
                h, n = hashlib.sha256(), 0
                while True:
                    b = f.read(CHUNK)
                    if not b:
                        break
                    h.update(b)
                    n += len(b)
                seen[m.name] = {"sha256": h.hexdigest(), "size": n}
    stored = seen.pop("web/manifest.json", None)
    if stored is None or seen != manifest:
        bad = sorted(set(seen) ^ set(manifest)) or sorted(k for k in manifest if seen.get(k) != manifest[k])
        raise MaintError("archive_verify_failed", path=(bad[0] if bad else "web/manifest.json")[:200])


def summary(root: Path, jobs: list[dict], marks: dict) -> dict:
    try:
        st = audit.derive(root, jobs, marks)
    except Exception:  # the summary is a nicety: never block an archive on it
        return {"phases": {}, "findings": {}}
    phases = {p["key"]: p["status"] for p in st.get("phases", [])}
    return {"phases": phases, "findings": {k: v for k, v in st.get("findings_by_status", {}).items() if v}}


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name("." + path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        os.fchmod(f.fileno(), 0o640)
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def make_archive_handler(root: Path, data: Path, store: Path, db: JobDB, usage=None):
    """`usage(job_ids) -> (usd, [model, ...])`: what THESE jobs spent and with which models (ledger)."""
    root, data, store = Path(root), Path(data), Path(store)

    def archive(job: dict, secret: bytearray | None, log) -> dict:
        if not secret:
            raise MaintError("password_required")
        if not gpgbox.available():
            raise MaintError("gpg_missing")
        if not store.is_dir() or not os.access(store, os.W_OK | os.X_OK):
            raise MaintError("archive_store_missing")
        project = project_name(root)
        files = collect(root, log)
        if not any(rel != "codecome.yml" for rel, _ in files):
            raise MaintError("nothing_to_archive")
        check_writable(root)

        aid = f"{slug(project)}-{time.strftime('%Y%m%d-%H%M%S')}"
        while (store / f"{aid}.json").exists() or (store / f"{aid}.tar.gz.gpg").exists():
            time.sleep(1)
            aid = f"{slug(project)}-{time.strftime('%Y%m%d-%H%M%S')}"
        final, part = store / f"{aid}.tar.gz.gpg", store / f".{aid}.tar.gz.gpg.part"

        phase_jobs = [j for j in db.list_jobs(None) if j["action"] in ACTIONS and j["status"] not in ("queued", "running")]
        marks = db.export_marks()
        info = summary(root, phase_jobs, db.marks())
        usd, models = None, []
        if usage is not None and phase_jobs:
            try:  # bookkeeping only: never block an archive on it
                spent, used = usage([j["id"] for j in phase_jobs])
                usd, models = round(spent, 4), [str(m)[:60] for m in used][:6]
            except Exception:
                usd, models = None, []

        log(f"archive {aid}: {len(files)} files, {len(phase_jobs)} jobs, {len(marks)} assumed")
        manifest: dict[str, dict] = {}
        total = 0
        try:
            with gpgbox.encrypt_to(part, secret) as w:
                with tarfile.open(fileobj=w, mode="w|gz", format=tarfile.PAX_FORMAT) as tf:
                    for i, (rel, p) in enumerate(files, 1):
                        total += _add_file(tf, "workspace/" + rel, p, manifest)
                        if i % 500 == 0:
                            log(f"  {i}/{len(files)} files")
                    _add_bytes(tf, "web/jobs.json", json.dumps(phase_jobs, ensure_ascii=False, indent=1).encode(), manifest)
                    _add_bytes(tf, "web/marks.json", json.dumps(marks, ensure_ascii=False, indent=1).encode(), manifest)
                    for j in phase_jobs:
                        lp = data / "logs" / f"{j['id']}.log"
                        if lp.is_file() and not lp.is_symlink():
                            _add_file(tf, f"web/logs/{j['id']}.log", lp, manifest)
                    _add_bytes(tf, "web/manifest.json", json.dumps(manifest, indent=1, sort_keys=True).encode(), {})
            log("encrypted; verifying by decrypting it again")
            verify(part, secret, manifest)
        except gpgbox.GpgError as e:
            part.unlink(missing_ok=True)
            log(f"gpg: {e.detail.strip()[:500]}")
            raise MaintError(e.code)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        size, digest = part.stat().st_size, sha256_file(part)
        os.replace(part, final)
        meta = {"format": FORMAT, "id": aid, "project": project, "created_at": time.time(),
                "by": job.get("requested_by"), "file": final.name, "size": size, "sha256": digest,
                "files": len(files), "bytes": total, "jobs": len(phase_jobs), "assumed": len(marks),
                "cost_usd": usd, "models": models, **info}
        _write_json(store / f"{aid}.json", meta)
        log(f"verified ✓ {final.name} ({size} bytes, sha256 {digest[:16]}…)")

        removed = empty_workspace(root)
        db.tag_audit(aid)
        db.replace_marks([])
        log(f"workspace emptied: {removed} files removed")
        return {"archive": aid, "files": len(files), "bytes": total, "size": size}

    return archive


# ---------------- rescue ----------------

RESCUE_LIMITS = Limits(max_entries=1_000_000, max_file=8 * 1024 ** 3, max_total=64 * 1024 ** 3)


def _hash_tree(base: Path) -> dict[str, dict]:
    out = {}
    for dirpath, _, filenames in os.walk(base, followlinks=False):
        for n in filenames:
            p = Path(dirpath) / n
            rel = p.relative_to(base).as_posix()
            if rel == "web/manifest.json":
                continue
            h, size = hashlib.sha256(), 0
            with open(p, "rb") as f:
                for b in iter(lambda: f.read(1 << 20), b""):
                    h.update(b)
                    size += len(b)
            out[rel] = {"sha256": h.hexdigest(), "size": size}
    return out


def _load_json(path: Path, default):
    try:
        if path.is_symlink() or path.stat().st_size > 64 * 1024 * 1024:
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _valid_marks(raw) -> list[dict]:
    out = []
    for m in raw if isinstance(raw, list) else []:
        if (isinstance(m, dict) and isinstance(m.get("finding"), str) and re.fullmatch(r"CC-\d{1,6}", m["finding"])
                and m.get("phase") in ("4", "5") and m.get("mark") == "assumed" and isinstance(m.get("by"), str)
                and isinstance(m.get("at"), (int, float)) and isinstance(m.get("reason", ""), str)):
            out.append({k: m.get(k, "") for k in ("finding", "phase", "mark", "by", "at", "reason")})
    return out


def make_reset_handler(root: Path, db: JobDB):
    """Empty the workspace WITHOUT archiving (the admin confirmed it is not worth keeping).
    Same emptying as an archive; the audit's jobs are set aside as discarded, marks cleared.
    The usage ledger is never touched (money spent stays on record)."""
    root = Path(root)

    def reset(job: dict, secret, log) -> dict:
        if not has_content(root):
            raise MaintError("nothing_to_reset")
        check_writable(root)
        project = project_name(root)
        tag = f"discarded-{time.strftime('%Y%m%d-%H%M%S')}"
        removed = empty_workspace(root)
        jobs = db.tag_audit(tag)
        db.replace_marks([])
        log(f"workspace emptied without archive: {removed} files removed, {jobs} jobs set aside as {tag}")
        return {"files": removed, "jobs": jobs, "project": project}

    return reset


def _same_yaml(a: Path, b: Path) -> bool:
    """Same configuration (quoting, spacing or comments aside)?"""
    import yaml
    try:
        return yaml.safe_load(a.read_text(encoding="utf-8")) == yaml.safe_load(b.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return a.read_bytes() == b.read_bytes()


def make_rescue_handler(root: Path, store: Path, db: JobDB):
    root, store = Path(root), Path(store)

    def rescue(job: dict, secret: bytearray | None, log) -> dict:
        aid = job.get("target")
        if not isinstance(aid, str) or not ID_RE.match(aid):
            raise MaintError("archive_not_found")
        enc = store / f"{aid}.tar.gz.gpg"
        if enc.is_symlink() or not enc.is_file():
            raise MaintError("archive_not_found")
        if not secret:
            raise MaintError("password_required")
        if not gpgbox.available():
            raise MaintError("gpg_missing")
        if has_content(root):
            raise MaintError("workspace_not_empty")
        check_writable(root)

        tmp = root / f"{STAGING_PREFIX}rescue-{secrets.token_hex(6)}"  # swept at worker start if we crash
        log(f"rescue {aid}: decrypting and checking every entry")
        try:
            with gpgbox.decrypt_from(enc, secret) as r:
                rep = extract_tar_stream(r, tmp, limits=RESCUE_LIMITS, roots=("workspace", "web"))
        except gpgbox.GpgError as e:
            shutil.rmtree(tmp, ignore_errors=True)
            if e.code != "bad_passphrase":
                log(f"gpg: {e.detail.strip()[:500]}")
            raise MaintError(e.code)
        try:
            manifest = _load_json(tmp / "web/manifest.json", None)
            if not isinstance(manifest, dict) or _hash_tree(tmp) != manifest:
                raise MaintError("archive_verify_failed", path="web/manifest.json")
            log(f"verified ✓ {rep.files} files")

            ws = tmp / "workspace"
            moves = []
            for top in TOP_DIRS:
                base = ws / top
                for dirpath, _, filenames in os.walk(base, followlinks=False):
                    for n in filenames:
                        src = Path(dirpath) / n
                        rel = src.relative_to(ws)
                        if (root / rel).exists() or (root / rel).is_symlink():
                            raise MaintError("workspace_not_empty")
                        moves.append((src, root / rel))
            for src, dst in moves:
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.rename(src, dst)

            config = "none"
            cfg_new, cfg_cur = ws / "codecome.yml", root / "codecome.yml"
            if cfg_new.is_file():
                if not cfg_cur.exists():
                    os.rename(cfg_new, cfg_cur)
                    config = "restored"
                else:
                    # The NAME belongs to the audit: it always comes back. The rest of the file is
                    # CodeCome's configuration: if it changed since, it is never overwritten.
                    old_name = read_name(cfg_new.read_text(encoding="utf-8", errors="replace"))
                    if old_name:
                        try:
                            if set_name(cfg_cur, old_name):
                                log(f"project name restored: {old_name}")
                        except (ProjectNameError, OSError):
                            log("could not restore the project name in codecome.yml")
                    if _same_yaml(cfg_cur, cfg_new):
                        config = "same"
                    else:
                        keep = root / "codecome.yml.archived"
                        if not keep.exists():
                            os.rename(cfg_new, keep)
                        config = "kept_current"
                        log("codecome.yml differs from the archived one: current kept, archived copy in codecome.yml.archived")

            marks = _valid_marks(_load_json(tmp / "web/marks.json", []))
            db.replace_marks(marks)
            back = db.untag_audit(aid)
            log(f"restored {len(moves)} files, {back} jobs, {len(marks)} assumed")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return {"archive": aid, "files": len(moves), "jobs": back, "assumed": len(marks), "config": config}

    return rescue

