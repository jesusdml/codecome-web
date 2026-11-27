# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""v1.1 step 3: Upload code. The web stores the raw archive; the worker extracts it into src/."""
import io
import stat
import tarfile
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import main, oneshot, uploads
from app.auth import Auth, hash_password
from app.jobs import JobDB
from app.maintenance import make_handlers
from app.runner import Runner
from tests.test_api import PW
from tests.test_safe_extract import raw_zip

pytestmark = pytest.mark.real_src


def zip_bytes(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, d in files.items():
            zf.writestr(n, d)
    return b.getvalue()


@pytest.fixture()
def env(monkeypatch, tmp_path):
    root, data = tmp_path / "CodeCome", tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(main, "CODECOME_ROOT", root)
    monkeypatch.setattr(main, "UPLOADS_DIR", data / "uploads")
    monkeypatch.setattr(main, "SECRETS_DIR", data / "secrets")
    monkeypatch.setattr(main, "UPLOAD_MAX", 1024 * 1024)
    monkeypatch.setattr(main, "auth", Auth({"ana": hash_password(PW), "bob": hash_password(PW)}, b"k" * 32))
    monkeypatch.setattr(main, "ADMINS", {"ana"})
    monkeypatch.setattr(main, "jobs", JobDB(data / "jobs.db"))
    return root, data


def client(user="ana"):
    c = TestClient(main.app)
    assert c.post("/api/login", json={"username": user, "password": PW}).status_code == 200
    return c


def put(c, body):
    return c.put("/api/upload", content=body, headers={"Content-Type": "application/octet-stream"})


def work(root, data):
    """Run the next queued job as the worker would."""
    runner = Runner(main.jobs, root, maintenance=make_handlers(root, data), secrets_dir=data / "secrets")
    job = main.jobs.claim_next(data / "logs")
    runner.run(job)
    return main.jobs.get(job["id"])


# ---------- API ----------

def test_full_upload_flow(env):
    root, data = env
    c = client()
    assert c.get("/api/settings").json()["src_empty"] is True
    r = put(c, zip_bytes({"proj-main/main.c": b"int main(){}", "proj-main/lib/a.h": b"//"}))
    assert r.status_code == 201
    uid = r.json()["id"]
    part = data / "uploads" / f"{uid}.part"
    assert stat.S_IMODE(part.stat().st_mode) == 0o640
    jid = c.post(f"/api/upload/{uid}/extract", json={"project": "Nueva auditoría"}).json()["id"]
    job = work(root, data)
    assert job["id"] == jid and job["status"] == "done", job
    assert job["result"]["files"] == 2 and job["result"]["stripped"] == "proj-main"
    assert (root / "src/main.c").read_bytes() == b"int main(){}"
    assert job["result"]["project"] == "Nueva auditoría"
    assert "Nueva auditoría" in (root / "codecome.yml").read_text()
    assert not part.exists()
    assert c.get("/api/settings").json()["src_empty"] is False
    assert put(c, zip_bytes({"a": b"1"})).json()["detail"]["code"] == "src_not_empty"


def test_only_admins(env):
    r = put(client("bob"), zip_bytes({"a": b"1"}))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "upload_forbidden"


def test_cross_origin_refused(env):
    r = client().put("/api/upload", content=b"PK\x03\x04", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_size_cap_and_magic(env):
    root, data = env
    c = client()
    r = put(c, b"PK\x03\x04" + b"\x00" * (1024 * 1024))
    assert r.status_code == 413 and r.json()["detail"]["code"] == "upload_too_large"
    r = put(c, b"#!/bin/sh\nrm -rf /\n")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "not_archive"
    assert put(c, b"").json()["detail"]["code"] == "upload_empty"
    assert list((data / "uploads").iterdir()) == []                       # nothing left behind


def test_one_pending_upload_at_a_time(env):
    root, data = env
    c = client()
    a = put(c, zip_bytes({"a": b"1"})).json()["id"]
    b = put(c, zip_bytes({"b": b"2"})).json()["id"]
    assert [p.name for p in (data / "uploads").iterdir()] == [f"{b}.part"]
    assert c.post(f"/api/upload/{a}/extract", json={"project": "Nueva auditoría"}).status_code == 404


@pytest.mark.parametrize("uid", ["../../etc/passwd", "x" * 32, "0" * 31])
def test_extract_bad_ids(env, uid):
    assert client().post(f"/api/upload/{uid}/extract", json={"project": "Nueva auditoría"}).status_code in (404, 405)


def test_refused_while_jobs_active(env):
    root, data = env
    (root / "src").mkdir()
    main.jobs.enqueue_maintenance("archive", "ana")
    r = put(client(), zip_bytes({"a": b"1"}))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "jobs_active"


def test_password_travels_by_one_shot_file_only(env):
    root, data = env
    c = client()
    body = raw_zip(data.parent / "enc.zip", [("app.py", b"print(1)\n", 0o100644)], pwd=b"zip-secret-77").read_bytes()
    uid = put(c, body).json()["id"]
    jid = c.post(f"/api/upload/{uid}/extract", json={"password": "zip-secret-77", "project": "Cifrada"}).json()["id"]
    files = list((data / "secrets").iterdir())
    assert len(files) == 1 and files[0].read_bytes() == b"zip-secret-77"
    assert b"zip-secret-77" not in (data / "jobs.db").read_bytes()
    job = work(root, data)
    assert job["status"] == "done" and (root / "src/app.py").exists()
    assert list((data / "secrets").iterdir()) == []
    assert "zip-secret-77" not in (data / "logs" / f"{jid}.log").read_text()


# ---------- worker handler ----------

def test_wrong_password_keeps_the_upload_and_an_empty_src(env):
    root, data = env
    (root / "src").mkdir()
    (root / "src/.gitkeep").write_text("")
    c = client()
    body = raw_zip(data.parent / "enc.zip", [("app.py", b"print(1)\n" * 50, 0o100644)], pwd=b"right-one").read_bytes()
    uid = put(c, body).json()["id"]
    c.post(f"/api/upload/{uid}/extract", json={"password": "wrong-one", "project": "Cifrada"})
    job = work(root, data)
    assert job["status"] == "failed" and job["error"]["code"] == "extract_bad_password"
    assert (data / "uploads" / f"{uid}.part").exists()                    # retry possible
    assert (root / "src").is_dir() and list((root / "src").iterdir()) == []
    c.post(f"/api/upload/{uid}/extract", json={"password": "right-one", "project": "Cifrada"})
    again = work(root, data)
    assert again["status"] == "done", again["error"]


def test_hostile_archive_is_refused_and_deleted(env):
    root, data = env
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w:gz") as tf:
        ti = tarfile.TarInfo("src-link")
        ti.type, ti.linkname = tarfile.SYMTYPE, "/etc"
        tf.addfile(ti)
    c = client()
    uid = put(c, b.getvalue()).json()["id"]
    c.post(f"/api/upload/{uid}/extract", json={"project": "Nueva auditoría"})
    job = work(root, data)
    assert job["error"]["code"] == "extract_link"
    assert not (data / "uploads" / f"{uid}.part").exists()
    assert not (root / "src").exists()


def test_handler_refuses_when_src_got_code_meanwhile(env):
    root, data = env
    c = client()
    uid = put(c, zip_bytes({"a": b"1"})).json()["id"]
    c.post(f"/api/upload/{uid}/extract", json={"project": "Nueva auditoría"})
    (root / "src").mkdir()
    (root / "src/existing.c").write_text("x")
    job = work(root, data)
    assert job["error"]["code"] == "src_not_empty"
    assert (root / "src/existing.c").read_text() == "x"


def test_uploads_sweep(tmp_path):
    import os, time
    old = tmp_path / ("a" * 32 + ".part")
    new = tmp_path / ("b" * 32 + ".part")
    other = tmp_path / "keep.txt"
    for p in (old, new, other):
        p.write_text("x")
    os.utime(old, (time.time() - 90000,) * 2)
    assert uploads.clear(tmp_path, max_age=uploads.MAX_AGE_S) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([new.name, other.name])


@pytest.mark.parametrize("name", ["", "   ", "../etc", "a\nb", "x" * 81, "name: injected", "#comment"])
def test_new_project_name_is_validated(env, name):
    root, data = env
    c = client()
    uid = put(c, zip_bytes({"a": b"1"})).json()["id"]
    r = c.post(f"/api/upload/{uid}/extract", json={"project": name})
    assert r.status_code in (400, 422)


def test_new_name_edits_only_that_line(env):
    root, data = env
    cfg = 'project:\n  name: viejo\n\n# CodeCome config\naudit:\n  scope:\n    include: ["src/**"]\n'
    (root / "codecome.yml").write_text(cfg)
    c = client()
    uid = put(c, zip_bytes({"a.py": b"1"})).json()["id"]
    c.post(f"/api/upload/{uid}/extract", json={"project": "Tienda v2"})
    assert work(root, data)["status"] == "done"
    assert (root / "codecome.yml").read_text() == cfg.replace("name: viejo", 'name: "Tienda v2"')
    assert c.get("/api/settings").json()["archive_confirm"] == "Tienda v2"
