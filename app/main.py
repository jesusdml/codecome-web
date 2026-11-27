# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Web API over CodeCome's itemdb.

Everything that returns data needs a session. The bind address and port come from
the service unit (run.sh for development: loopback only).
"""

from __future__ import annotations

import os
from pathlib import Path

import asyncio
import codecs
import json
import re
import time
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .auth import COOKIE, SESSION_TTL, Auth
from .itemdb import STATUSES, FindingNotFound, InvalidFindingId, ItemDB, validate_id
from .ledger import GROUPS, Ledger
from .audit_view import build_audit, build_phase, project_name
from .docs import DocError, Docs, md_tree, read_safe
from . import archive_store, oneshot, uploads
from .archive import has_content
from .project_cfg import ProjectNameError, clean_name as clean_project_name
from .gates import BLOCKING, GateRunner, launch_reasons, src_has_code
from .jobs import ACTIONS, FINAL, DuplicateJob, InvalidJob, JobDB, build_argv
from .profiles import Profiles, ProfileError
from .settings import Settings, SettingsError, validate as validate_settings

BASE = Path(__file__).resolve().parent.parent
CODECOME_ROOT = Path(os.environ.get("CODECOME_ROOT", "/opt/CodeCome"))
DATA_DIR = Path(os.environ.get("CODECOME_DATA", BASE / "data"))

app = FastAPI(title="CodeCome Web", docs_url=None, redoc_url=None, openapi_url=None)
db = ItemDB(CODECOME_ROOT)
docs = Docs(CODECOME_ROOT)
auth = Auth.from_env(DATA_DIR)
jobs = JobDB(DATA_DIR / "jobs.db")
SECRETS_DIR = DATA_DIR / "secrets"  # one-shot passwords for the worker (app/oneshot.py)
UPLOADS_DIR = DATA_DIR / "uploads"  # archives waiting for the worker to extract them into src/
ARCHIVES_DIR = Path(os.environ.get("CODECOME_ARCHIVES", "/opt/codecome-archives"))  # written by the worker only
UPLOAD_MAX = int(os.environ.get("CODECOME_UPLOAD_MAX_MB", "500")) * 1024 * 1024
gates = GateRunner(CODECOME_ROOT)
ledger = Ledger(DATA_DIR / "ledger.db", readonly=True)  # only the worker writes it
LANGS = ("en", "es", "de", "fr", "ca", "gl", "eu")  # closed list: a locale file name is never built from request text
settings = Settings(DATA_DIR / "settings.json")
profiles = Profiles(Path(os.environ.get("CODECOME_PROFILES", BASE / "config" / "profiles.json")), settings, LANGS)
# Who may change Settings. Unset = any signed-in user (single-team install).
ADMINS = {u.strip() for u in os.environ.get("CODECOME_ADMINS", "").split(",") if u.strip()}
LOG_DIR = DATA_DIR / "logs"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def api_error(status: int, code: str, /, **params) -> HTTPException:
    """Errors carry a stable code; the UI translates them into the chosen language."""
    return HTTPException(status_code=status, detail={"code": code, "params": params})


LOCALES = Path(__file__).with_name("locales")


@app.get("/api/ui")
def ui_config():
    """Public (the login page needs it): only the default language and the list of languages."""
    return {"default_lang": profiles.default_lang() or "en", "langs": list(LANGS)}


@app.get("/locales/{lang}.json")
def locale(lang: str):
    if lang not in LANGS:  # closed list: a path is never built from request text
        raise api_error(404, "locale_not_found")
    return JSONResponse(json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8")))


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    # No inline code anywhere in the front end, so no 'unsafe-inline'.
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    return resp


def require_user(request: Request) -> str:
    user = auth.read_token(request.cookies.get(COOKIE))
    if not user:
        raise api_error(401, "not_authenticated")
    return user


class LoginBody(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/login")
def login(body: LoginBody, request: Request, response: Response):
    ip = request.client.host if request.client else "?"
    if auth.locked(ip, body.username):
        raise api_error(429, "too_many_attempts")
    if not auth.check_login(ip, body.username, body.password):
        raise api_error(401, "bad_credentials")
    response.set_cookie(
        COOKIE, auth.make_token(body.username), max_age=SESSION_TTL,
        httponly=True, samesite="strict", secure=auth.secure_cookie, path="/",
    )
    return {"user": body.username}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


protected = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


@protected.get("/me")
def me(user: str = Depends(require_user)):
    return {"user": user}


@protected.get("/findings")
def list_findings(status: str | None = Query(default=None)):
    if status is not None and status not in STATUSES:
        raise api_error(400, "invalid_status")
    return {
        "counts": db.counts(),
        "findings": [f.summary() for f in db.list_findings(status)],
    }


@protected.get("/findings/{finding_id}")
def get_finding(finding_id: str):
    try:
        d = db.get_finding(finding_id).detail()
        # The body is agent-written markdown: send it as the same safe tree as the report.
        d["tree"] = md_tree(d.get("body") or "")
        d["has_recording"] = finding_id in docs.recordings()
        d["has_cast"] = docs.has_cast(finding_id)
        d["marks"] = {ph: m for (f, ph), m in jobs.marks().items() if f == finding_id}
        return d
    except InvalidFindingId:
        raise api_error(400, "invalid_finding_id")
    except FindingNotFound:
        raise api_error(404, "finding_not_found")


@protected.get("/report")
def get_report():
    try:
        r = docs.report()
        r["assumed"] = {f: {"phase": ph, **m} for (f, ph), m in sorted(jobs.marks().items())}
        r["project"] = project_name(CODECOME_ROOT)
        return r
    except DocError as e:
        raise api_error(404 if e.code == "doc_not_found" else 422, e.code)


@protected.get("/report.md")
def get_report_md():
    try:
        data = docs.report_raw()
    except DocError as e:
        raise api_error(404 if e.code == "doc_not_found" else 422, e.code)
    # Download only (attachment), never rendered by the browser: it is agent-written text.
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", project_name(CODECOME_ROOT) or "codecome").strip("-") or "codecome"
    return Response(data, media_type="text/markdown; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{name}-report.md"',
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@protected.get("/findings/{finding_id}/recording")
def get_cast(finding_id: str):
    try:
        return docs.recording_cast(finding_id)
    except InvalidFindingId:
        raise api_error(400, "invalid_finding_id")
    except DocError as e:
        raise api_error(404 if e.code == "doc_not_found" else 422, e.code)


@protected.get("/findings/{finding_id}/recording.gif")
def get_recording(finding_id: str):
    try:
        data = docs.recording_gif(finding_id)
    except InvalidFindingId:
        raise api_error(400, "invalid_finding_id")
    except DocError as e:
        raise api_error(404 if e.code == "doc_not_found" else 422, e.code)
    # Agent-written bytes: fixed type, no sniffing, never a document.
    return Response(data, media_type="image/gif", headers={
        "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline", "Cache-Control": "no-store"})


def _can_edit(user: str) -> bool:
    return not ADMINS or user in ADMINS


@protected.get("/settings")
def get_settings(user: str = Depends(require_user)):
    try:
        view = profiles.admin_view()
    except ProfileError as e:
        raise api_error(500, e.code, **e.params)
    raw = settings.read_raw()
    return {**view, "langs": list(LANGS), "default_lang": profiles.default_lang() or "en",
            "can_edit": _can_edit(user), "updated_by": raw.get("updated_by"), "updated_at": raw.get("updated_at"),
            "src_empty": not src_has_code(CODECOME_ROOT), "upload_max_mb": UPLOAD_MAX // (1024 * 1024),
            "project": project_name(CODECOME_ROOT), "archive_confirm": _archive_confirm(),
            "archive_min_password": ARCHIVE_MIN_PW}


def same_origin(request: Request) -> None:
    """Extra CSRF defence on the actions that change things."""
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        raise api_error(403, "origin_not_allowed")


def _upload_allowed(user: str) -> None:
    if not _can_edit(user):
        raise api_error(403, "upload_forbidden")
    if src_has_code(CODECOME_ROOT):
        raise api_error(409, "src_not_empty")
    active = next((j for j in jobs.list_jobs(None) if j["status"] in ("queued", "running")), None)
    if active:
        raise api_error(409, "jobs_active", id=active["id"], action=active["action"])


@protected.put("/upload", dependencies=[Depends(same_origin)], status_code=201)
async def put_upload(request: Request, user: str = Depends(require_user)):
    """Raw body (application/octet-stream), streamed to disk with a hard cap: never in memory."""
    _upload_allowed(user)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > UPLOAD_MAX:
        raise api_error(413, "upload_too_large", max_mb=UPLOAD_MAX // (1024 * 1024))
    try:
        upload_id, size = await uploads.receive(request.stream(), UPLOADS_DIR, UPLOAD_MAX)
    except uploads.UploadError as e:
        raise api_error(e.status, e.code, **e.params)
    uploads.clear(UPLOADS_DIR, keep=upload_id)  # only one pending upload at a time
    return {"id": upload_id, "size": size}


class ExtractBody(BaseModel):
    password: str | None = Field(default=None, max_length=1024)
    project: str = Field(max_length=200)  # name of the NEW audit (codecome.yml project.name)


@protected.post("/upload/{upload_id}/extract", dependencies=[Depends(same_origin)], status_code=201)
def extract_upload(upload_id: str, body: ExtractBody, user: str = Depends(require_user)):
    _upload_allowed(user)
    try:
        part = uploads.path_of(UPLOADS_DIR, upload_id)
    except uploads.UploadError as e:
        raise api_error(e.status, e.code)
    if not part.is_file():
        raise api_error(404, "upload_not_found")
    try:
        project = clean_project_name(body.project)
    except ProjectNameError as e:
        raise api_error(400, e.code)
    name = oneshot.put(SECRETS_DIR, body.password.encode("utf-8")) if body.password else None
    try:
        job_id = jobs.enqueue_maintenance("upload", user, target=upload_id, secret=name, params={"project": project})
    except (DuplicateJob, InvalidJob) as e:
        oneshot.discard(SECRETS_DIR, name)
        raise api_error(409 if isinstance(e, DuplicateJob) else 400, e.code, **e.params)
    return {"id": job_id}


ARCHIVE_MIN_PW = 12
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _archive_confirm() -> str:
    """What the admin must type to archive: the project name (or "codecome" without one)."""
    return project_name(CODECOME_ROOT) or "codecome"


class ArchiveBody(BaseModel):
    confirm: str = Field(max_length=200)
    password: str = Field(max_length=1024)


@protected.post("/archive", dependencies=[Depends(same_origin)], status_code=201)
def archive_audit(body: ArchiveBody, user: str = Depends(require_user)):
    """Archive & reset: the worker encrypts everything, verifies it and only then empties src/,
    itemdb/ and runs/. The password is never stored: lost password = unreadable archive."""
    if not _can_edit(user):
        raise api_error(403, "archive_forbidden")
    if body.confirm.strip() != _archive_confirm():
        raise api_error(400, "archive_confirm_mismatch")
    if len(body.password) < ARCHIVE_MIN_PW:
        raise api_error(400, "password_too_short", min=ARCHIVE_MIN_PW)
    if _CTRL.search(body.password):
        raise api_error(400, "password_invalid")
    name = oneshot.put(SECRETS_DIR, body.password.encode("utf-8"))
    try:
        job_id = jobs.enqueue_maintenance("archive", user, secret=name)
    except (DuplicateJob, InvalidJob) as e:
        oneshot.discard(SECRETS_DIR, name)
        raise api_error(409 if isinstance(e, DuplicateJob) else 400, e.code, **e.params)
    return {"id": job_id}


@protected.get("/archives")
def list_archives(user: str = Depends(require_user)):
    """The Archive screen: the unencrypted cards only (never the content)."""
    return {"archives": archive_store.list_archives(ARCHIVES_DIR), "can_download": _can_edit(user),
            "can_rescue": _can_edit(user), "workspace_empty": not has_content(CODECOME_ROOT)}


@protected.get("/archives/{archive_id}/file")
def download_archive(archive_id: str, user: str = Depends(require_user)):
    # Admins only: even encrypted, a copy can be attacked offline with password guesses.
    if not _can_edit(user):
        raise api_error(403, "archive_forbidden")
    try:
        path = archive_store.encrypted_file(ARCHIVES_DIR, archive_id)
    except archive_store.StoreError as e:
        raise api_error(e.status, e.code)
    return FileResponse(path, media_type="application/octet-stream", filename=path.name,
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


class ResetBody(BaseModel):
    confirm: str = Field(max_length=200)
    understood: bool = False


@protected.post("/reset", dependencies=[Depends(same_origin)], status_code=201)
def reset_audit(body: ResetBody, user: str = Depends(require_user)):
    """Empty the workspace WITHOUT an archive: everything the audit produced is deleted for good."""
    if not _can_edit(user):
        raise api_error(403, "archive_forbidden")
    if body.confirm.strip() != _archive_confirm():
        raise api_error(400, "archive_confirm_mismatch")
    if not body.understood:
        raise api_error(400, "reset_not_confirmed")
    if not has_content(CODECOME_ROOT):
        raise api_error(409, "nothing_to_reset")
    try:
        job_id = jobs.enqueue_maintenance("reset", user)
    except (DuplicateJob, InvalidJob) as e:
        raise api_error(409 if isinstance(e, DuplicateJob) else 400, e.code, **e.params)
    return {"id": job_id}


class RescueBody(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


@protected.post("/archives/{archive_id}/rescue", dependencies=[Depends(same_origin)], status_code=201)
def rescue_archive(archive_id: str, body: RescueBody, user: str = Depends(require_user)):
    """Bring an archived audit back into an EMPTY workspace (the worker decrypts, checks every
    entry and the manifest, then moves it in place). The archive itself is kept."""
    if not _can_edit(user):
        raise api_error(403, "archive_forbidden")
    try:
        archive_store.encrypted_file(ARCHIVES_DIR, archive_id)
    except archive_store.StoreError as e:
        raise api_error(e.status, e.code)
    if has_content(CODECOME_ROOT):
        raise api_error(409, "workspace_not_empty")
    if _CTRL.search(body.password):
        raise api_error(400, "password_invalid")
    name = oneshot.put(SECRETS_DIR, body.password.encode("utf-8"))
    try:
        job_id = jobs.enqueue_maintenance("rescue", user, target=archive_id, secret=name)
    except (DuplicateJob, InvalidJob) as e:
        oneshot.discard(SECRETS_DIR, name)
        raise api_error(409 if isinstance(e, DuplicateJob) else 400, e.code, **e.params)
    return {"id": job_id}


class JobBody(BaseModel):
    action: str = Field(max_length=16)
    finding: str | None = Field(default=None, max_length=16)
    profile: str | None = Field(default=None, max_length=24)
    model: str | None = Field(default=None, max_length=24)  # a catalog name, never a raw model id


@protected.post("/jobs", dependencies=[Depends(same_origin)], status_code=201)
def create_job(body: JobBody, user: str = Depends(require_user)):
    try:
        # The profile is an admin decision (Settings). Auditors always launch with the active one.
        if body.profile is not None and not _can_edit(user) and body.profile != profiles.resolve(None):
            raise api_error(403, "profile_forbidden")
        profile = profiles.resolve(body.profile)
        build_argv(body.action, body.finding)  # validates action and id before anything runs
        model = profiles.check_model(body.model, body.action)
        # D5: the UI is not the only guardian. Structural problems that `enqueue` already reports with
        # their own codes (wrong finding state, duplicate) pass through; the rest blocks here.
        reasons = launch_reasons(body.action, body.finding, root=CODECOME_ROOT, jobs=jobs.list_jobs(None),
                                 itemdb=db, gates=gates)
        if any(r["code"] in BLOCKING for r in reasons):
            raise api_error(409, "launch_blocked", reasons=reasons)
        job_id = jobs.enqueue(body.action, body.finding, user, db, profile, model)
    except DuplicateJob as e:
        raise api_error(409, e.code, **e.params)
    except ProfileError as e:
        raise api_error(400, e.code, **e.params)
    except (InvalidJob, InvalidFindingId) as e:
        raise api_error(400, getattr(e, "code", "invalid_finding_id"), **getattr(e, "params", {}))
    return {"id": job_id}


@protected.put("/settings", dependencies=[Depends(same_origin)])
def put_settings(body: dict, user: str = Depends(require_user)):
    if not _can_edit(user):
        raise api_error(403, "settings_forbidden")
    try:
        base = profiles.base()
        clean = validate_settings(body, catalog=set(base["models"]), profiles=set(base["profiles"]), langs=LANGS)
    except ProfileError as e:
        raise api_error(500, e.code, **e.params)
    except SettingsError as e:
        raise api_error(400, e.code, **e.params)
    settings.write(clean, user)
    return get_settings(user)


class AssumeBody(BaseModel):
    phase: str = Field(max_length=1)
    reason: str = Field(min_length=1, max_length=2000)  # why it is not proven: required, shown as text


ASSUME_NEEDS = {"4": "PENDING", "5": "CONFIRMED"}


@protected.post("/findings/{finding_id}/assume", dependencies=[Depends(same_origin)])
def assume_finding(finding_id: str, body: AssumeBody, user: str = Depends(require_user)):
    """Accept a finding without validating (4) or exploiting (5) it. Web-side mark only."""
    if body.phase not in ASSUME_NEEDS:
        raise api_error(400, "invalid_phase")
    try:
        f = db.get_finding(finding_id)
    except InvalidFindingId:
        raise api_error(400, "invalid_finding_id")
    except FindingNotFound:
        raise api_error(404, "finding_not_found")
    if f.status != ASSUME_NEEDS[body.phase]:
        raise api_error(409, "finding_wrong_state", action=f"phase-{body.phase}", needs=ASSUME_NEEDS[body.phase])
    busy = next((j for j in jobs.list_jobs(None, finding=f.id) if j["status"] in ("queued", "running")), None)
    if busy:
        raise api_error(409, "finding_busy", id=busy["id"])
    reason = "".join(ch for ch in body.reason if ch in "\n\t" or ch.isprintable()).strip()
    if not reason:
        raise api_error(400, "reason_required")
    jobs.set_mark(f.id, body.phase, user, reason)
    return {"ok": True}


@protected.delete("/findings/{finding_id}/assume/{phase}", dependencies=[Depends(same_origin)])
def unassume_finding(finding_id: str, phase: str):
    if phase not in ASSUME_NEEDS:
        raise api_error(400, "invalid_phase")
    try:
        validate_id(finding_id)
    except InvalidFindingId:
        raise api_error(400, "invalid_finding_id")
    return {"ok": jobs.clear_mark(finding_id, phase)}


@protected.get("/jobs")
def list_jobs(action: str | None = Query(None, max_length=20), finding: str | None = Query(None, max_length=20),
              status: str | None = Query(None, max_length=20),
              limit: int | None = Query(None, ge=1, le=100000)):
    if action is not None and action not in ACTIONS:
        raise api_error(400, "invalid_action")
    if finding is not None:
        try:
            validate_id(finding)
        except InvalidFindingId:
            raise api_error(400, "invalid_finding_id")
    # No filter: the classic "latest 50". With a filter the caller wants them all unless it asks for less.
    if limit is None and action is None and finding is None and status is None:
        limit = 50
    try:
        prof = profiles.public()
    except ProfileError as e:
        raise api_error(500, e.code, **e.params)
    return {"actions": list(ACTIONS), "worker": jobs.worker_state(), "jobs": jobs.list_jobs(limit, action=action, finding=finding, status=status),
            "profiles": prof["profiles"], "default_profile": prof["default"], "models": prof["models"]}


@protected.get("/audit")
def get_audit():
    try:
        prof = profiles.public()
    except ProfileError as e:
        raise api_error(500, e.code, **e.params)
    return build_audit(root=CODECOME_ROOT, jobs=jobs.list_jobs(None), itemdb=db, gates=gates, marks=jobs.marks(),
                       worker=jobs.worker_state(), profiles=prof)


@protected.get("/phases/{key}")
def get_phase(key: str):
    data = build_phase(key, root=CODECOME_ROOT, jobs=jobs.list_jobs(None))
    if data is None:
        raise api_error(404, "phase_not_found")
    return data


@protected.get("/usage")
def get_usage(project: str | None = Query(None, max_length=200), since: float | None = Query(None, alias="from"),
              until: float | None = Query(None, alias="to"), group: str = Query("phase"),
              real: bool = Query(False), price_as: str | None = Query(None, max_length=100),
              synthetic: bool = Query(False), scope: str = Query("all", pattern="^(all|current)$")):
    """`scope=current`: only the jobs of the audit in the workspace now (archived, discarded and
    other audits' spending stays in the ledger, it just is not this audit's)."""
    if group not in GROUPS:
        raise api_error(400, "invalid_group")
    ids = [j["id"] for j in jobs.list_jobs(None) if j["action"] in ACTIONS] if scope == "current" else None
    return ledger.usage(project=project, since=since, until=until, group=group, real=real,
                        price_as=price_as, include_synthetic=synthetic, job_ids=ids)


@protected.get("/jobs/{job_id}")
def get_job(job_id: int):
    job = jobs.get(job_id)
    if not job:
        raise api_error(404, "job_not_found")
    live = job.get("live")
    # Age of the last pulse measured on the server (the browser's clock may differ).
    job["live_age"] = round(time.time() - live["t"], 1) if live and job["status"] == "running" else None
    return {"worker": jobs.worker_state(), "job": job}


@protected.post("/jobs/{job_id}/cancel", dependencies=[Depends(same_origin)])
def cancel_job(job_id: int):
    name = jobs.secret_name(job_id)  # read before: a queued maintenance job never gets to take it
    if not jobs.request_cancel(job_id):
        raise api_error(409, "job_not_active")
    if name and (jobs.get(job_id) or {}).get("status") == "cancelled":
        oneshot.discard(SECRETS_DIR, name)
    return {"ok": True}


def _log_file(job: dict) -> Path | None:
    """Only a log living inside LOG_DIR is served (never a path from the user)."""
    if not job.get("log_path"):
        return None
    p = Path(job["log_path"]).resolve()
    try:
        p.relative_to(LOG_DIR.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


async def _log_stream(request: Request, job_id: int, offset: int):
    dec = codecs.getincrementaldecoder("utf-8")("replace")
    while not await request.is_disconnected():
        job = jobs.get(job_id)  # state first, then the file: if it has finished, the log is complete
        if not job:
            return
        data = b""
        path = _log_file(job)
        if path:
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read(65536)
        if data:
            offset += len(data)
            text = ANSI_RE.sub("", dec.decode(data))
            yield f"id: {offset}\nevent: log\ndata: {json.dumps(text)}\n\n"
            continue
        if job["status"] in FINAL:
            yield "event: end\ndata: {}\n\n"
            return
        await asyncio.sleep(0.5)


@protected.get("/jobs/{job_id}/log")
async def job_log(job_id: int, request: Request, offset: int = Query(default=0, ge=0)):
    if not jobs.get(job_id):
        raise api_error(404, "job_not_found")
    last = request.headers.get("last-event-id", "")
    if last.isdigit():
        offset = int(last)  # resume where it stopped
    return StreamingResponse(
        _log_stream(request, job_id, offset), media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no"},
    )


app.include_router(protected)



STATIC = Path(__file__).with_name("static")


TLS_CA = Path(os.environ.get("CODECOME_TLS_CA", "/etc/codecome-web/tls/ca.crt"))


@app.get("/ca.crt")
def ca_certificate():
    """The PUBLIC certificate of the local CA (never its key), to install it in browsers once.
    Public on purpose: it is needed before anyone can trust this site."""
    try:
        data = read_safe(TLS_CA, 16 * 1024)
    except DocError:
        raise api_error(404, "ca_not_found")
    if b"-----BEGIN CERTIFICATE-----" not in data or b"PRIVATE KEY" in data:
        raise api_error(404, "ca_not_found")
    return Response(data, media_type="application/x-x509-ca-cert", headers={
        "Content-Disposition": 'attachment; filename="codecome-lan-ca.crt"', "X-Content-Type-Options": "nosniff"})


@app.get("/", response_class=HTMLResponse)
@app.get("/ui", response_class=HTMLResponse)
def index():
    # Static HTML only: data still requires a session.
    return (STATIC / "index.html").read_text(encoding="utf-8")


# Public like the legacy page: code and styles only, never data. Symlinks are not followed.
app.mount("/static", StaticFiles(directory=STATIC), name="static")
