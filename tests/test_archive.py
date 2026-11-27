# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""v1.1 step 4: Archive & reset with a REAL gpg. Archive -> verify -> metadata -> empty, and on any
failure before the verification passes, the workspace is left exactly as it was."""
import json
import os
import stat
import tarfile

import pytest
from fastapi.testclient import TestClient

from app import archive as arch, gpgbox, main, oneshot
from app.auth import Auth, hash_password
from app.itemdb import ItemDB
from app.jobs import JobDB
from app.maintenance import make_handlers
from app.runner import MaintError, Runner
from tests.test_api import FIX, PW

pytestmark = [pytest.mark.real_src, pytest.mark.skipif(not gpgbox.available(), reason="needs gpg")]
PASS = b"archive-pass-1234"


def tree(root):
    return sorted(p.relative_to(root).as_posix() + ("/" if p.is_dir() else "") for p in root.rglob("*"))


@pytest.fixture()
def ws(tmp_path):
    root, data, store = tmp_path / "CodeCome", tmp_path / "data", tmp_path / "archives"
    for d in ("src", "itemdb/findings/PENDING", "itemdb/notes", "itemdb/reports", "itemdb/codeql/runs/r1", "runs"):
        (root / d).mkdir(parents=True)
    for p in ("src/.gitkeep", "itemdb/findings/PENDING/.gitkeep", "itemdb/notes/.gitkeep",
              "itemdb/reports/.gitkeep", "runs/.gitkeep"):
        (root / p).write_text("")
    (root / "codecome.yml").write_text("project:\n  name: Demo App\n")
    (root / "itemdb/README.md").write_text("# itemdb\n")
    (root / "src/app.py").write_text("print('hi')\n")
    (root / "src/run.sh").write_text("#!/bin/sh\n")
    os.chmod(root / "src/run.sh", 0o755)
    (root / "itemdb/findings/PENDING/CC-0001-x.md").write_text("---\nid: CC-0001\n---\n")
    (root / "itemdb/notes/threat-model.md").write_text("notes")
    (root / "itemdb/reports/report.md").write_text("# report")
    (root / "itemdb/codeql/runs/r1/out.sarif").write_text("{}")
    (root / "runs/summary.json").write_text("{}")
    os.symlink("/etc/passwd", root / "itemdb/notes/evil")       # never followed, never archived
    store.mkdir()
    db = JobDB(data / "jobs.db")
    jid = db.enqueue("phase-1", None, "ana", ItemDB(FIX))
    db.claim_next(data / "logs")
    (data / "logs").mkdir(parents=True, exist_ok=True)
    (data / "logs" / f"{jid}.log").write_text("phase 1 output\n")
    db.finish(jid, "done", 0)
    db.set_mark("CC-0001", "4", "ana", "management")
    return root, data, store, db, jid


def run_archive(root, data, store, db, password=PASS):
    name = oneshot.put(data / "secrets", password)
    aid = db.enqueue_maintenance("archive", "ana", secret=name)
    h = make_handlers(root, data, store=store, db=db, cost=lambda project, since: 1.23456)
    Runner(db, root, maintenance=h, secrets_dir=data / "secrets").run(db.claim_next(data / "logs"))
    return db.get(aid)


def members(path, password=PASS):
    out = {}
    with gpgbox.decrypt_from(path, password) as r, tarfile.open(fileobj=r, mode="r|gz") as tf:
        for m in tf:
            out[m.name] = (m.mode, tf.extractfile(m).read())
    return out


def test_archive_roundtrip_and_reset(ws):
    root, data, store, db, jid = ws
    job = run_archive(root, data, store, db)
    assert job["status"] == "done", job
    aid = job["result"]["archive"]
    assert aid.startswith("demo-app-") and arch.ID_RE.match(aid)

    gpg_file, meta_file = store / f"{aid}.tar.gz.gpg", store / f"{aid}.json"
    assert stat.S_IMODE(gpg_file.stat().st_mode) == 0o640 == stat.S_IMODE(meta_file.stat().st_mode)
    assert [p.name for p in store.iterdir() if p.name.startswith(".")] == []   # no .part left
    meta = json.loads(meta_file.read_text())
    assert meta["project"] == "Demo App" and meta["by"] == "ana" and meta["cost_usd"] == 1.2346
    assert meta["sha256"] == arch.sha256_file(gpg_file) and meta["jobs"] == 1 and meta["assumed"] == 1
    assert meta["files"] == 8 and meta["findings"] == {"PENDING": 1}

    m = members(gpg_file)
    assert set(m) == {"workspace/codecome.yml", "workspace/src/app.py", "workspace/src/run.sh",
                      "workspace/itemdb/findings/PENDING/CC-0001-x.md", "workspace/itemdb/notes/threat-model.md",
                      "workspace/itemdb/reports/report.md", "workspace/itemdb/codeql/runs/r1/out.sarif",
                      "workspace/runs/summary.json", "web/jobs.json", "web/marks.json", f"web/logs/{jid}.log",
                      "web/manifest.json"}
    assert m["workspace/src/run.sh"][0] == 0o755 and m["workspace/src/app.py"][0] == 0o644
    assert json.loads(m["web/marks.json"][1])[0]["reason"] == "management"
    assert json.loads(m["web/jobs.json"][1])[0]["id"] == jid
    assert b"archive-pass" not in m["web/jobs.json"][1]

    # The workspace is back to CodeCome's skeleton; its configuration stays.
    assert tree(root) == ["codecome.yml", "itemdb/", "itemdb/README.md", "itemdb/findings/", "itemdb/findings/PENDING/",
                          "itemdb/findings/PENDING/.gitkeep", "itemdb/notes/", "itemdb/notes/.gitkeep", "itemdb/reports/",
                          "itemdb/reports/.gitkeep", "runs/", "runs/.gitkeep", "src/", "src/.gitkeep"]
    assert os.path.exists("/etc/passwd")                                        # the symlink went, not its target
    # The audit's jobs and marks moved with it.
    assert [j["action"] for j in db.list_jobs(None)] == ["archive"]
    assert [j["id"] for j in db.list_jobs(None, audit=aid)] == [jid]
    assert db.marks() == {}
    log = (data / "logs" / f"{job['id']}.log").read_text()
    assert "verified" in log and "archive-pass" not in log
    assert list((data / "secrets").iterdir()) == []


def test_wrong_password_cannot_open_it(ws):
    root, data, store, db, _ = ws
    aid = run_archive(root, data, store, db)["result"]["archive"]
    with pytest.raises(gpgbox.GpgError) as e:
        members(store / f"{aid}.tar.gz.gpg", b"not-the-password")
    assert e.value.code == "bad_passphrase"


def test_failed_verification_changes_nothing(ws, monkeypatch):
    root, data, store, db, jid = ws
    before = tree(root)

    def broken(*a, **k):
        raise MaintError("archive_verify_failed", path="workspace/src/app.py")
    monkeypatch.setattr(arch, "verify", broken)
    job = run_archive(root, data, store, db)
    assert job["status"] == "failed" and job["error"]["code"] == "archive_verify_failed"
    assert tree(root) == before and list(store.iterdir()) == []
    assert db.marks() and [j["id"] for j in db.list_jobs(None) if j["action"] == "phase-1"] == [jid]


def test_unwritable_directory_stops_before_anything(ws):
    root, data, store, db, _ = ws
    os.chmod(root / "itemdb/codeql/runs", 0o555)
    try:
        job = run_archive(root, data, store, db)
    finally:
        os.chmod(root / "itemdb/codeql/runs", 0o755)
    assert job["error"] == {"code": "workspace_not_writable", "params": {"path": "itemdb/codeql/runs"}}
    assert list(store.iterdir()) == [] and (root / "src/app.py").exists()


def test_nothing_to_archive_and_missing_store(ws, tmp_path):
    root, data, store, db, _ = ws
    assert run_archive(root, data, store, db)["status"] == "done"
    assert run_archive(root, data, store, db)["error"]["code"] == "nothing_to_archive"
    (root / "src/new.c").write_text("x")
    assert run_archive(root, data, tmp_path / "nope", db)["error"]["code"] == "archive_store_missing"


def test_verify_detects_a_tampered_member(ws, tmp_path):
    root, data, store, db, _ = ws
    p = tmp_path / "x.tar.gz.gpg"
    manifest = {}
    with gpgbox.encrypt_to(p, PASS) as w, tarfile.open(fileobj=w, mode="w|gz") as tf:
        arch._add_bytes(tf, "workspace/a", b"one", manifest)
        arch._add_bytes(tf, "web/manifest.json", b"{}", {})
    arch.verify(p, PASS, manifest)
    with pytest.raises(MaintError) as e:
        arch.verify(p, PASS, {**manifest, "workspace/a": {"sha256": "0" * 64, "size": 3}})
    assert e.value.code == "archive_verify_failed" and e.value.params["path"] == "workspace/a"


# ---------- API ----------

@pytest.fixture()
def api(monkeypatch, tmp_path, ws):
    root, data, store, db, _ = ws
    monkeypatch.setattr(main, "CODECOME_ROOT", root)
    monkeypatch.setattr(main, "SECRETS_DIR", data / "secrets")
    monkeypatch.setattr(main, "auth", Auth({"ana": hash_password(PW), "bob": hash_password(PW)}, b"k" * 32))
    monkeypatch.setattr(main, "ADMINS", {"ana"})
    monkeypatch.setattr(main, "jobs", db)

    def login(u):
        c = TestClient(main.app)
        assert c.post("/api/login", json={"username": u, "password": PW}).status_code == 200
        return c
    return login, data


@pytest.mark.parametrize("user, body, status, code", [
    ("bob", {"confirm": "Demo App", "password": "x" * 12}, 403, "archive_forbidden"),
    ("ana", {"confirm": "demo app", "password": "x" * 12}, 400, "archive_confirm_mismatch"),
    ("ana", {"confirm": "Demo App", "password": "x" * 11}, 400, "password_too_short"),
    ("ana", {"confirm": "Demo App", "password": "abc\ndef-ghijkl"}, 400, "password_invalid"),
])
def test_archive_api_refusals(api, user, body, status, code):
    login, data = api
    r = login(user).post("/api/archive", json=body)
    assert r.status_code == status and r.json()["detail"]["code"] == code
    assert not (data / "secrets").exists() or list((data / "secrets").iterdir()) == []


def test_archive_api_queues_with_a_one_shot_secret(api):
    login, data = api
    c = login("ana")
    s = c.get("/api/settings").json()
    assert s["archive_confirm"] == "Demo App" and s["archive_min_password"] == 12
    r = c.post("/api/archive", json={"confirm": " Demo App ", "password": "a long enough pw"})
    assert r.status_code == 201
    secrets = list((data / "secrets").iterdir())
    assert len(secrets) == 1 and secrets[0].read_bytes() == b"a long enough pw"
    again = c.post("/api/archive", json={"confirm": "Demo App", "password": "a long enough pw"})
    assert again.status_code == 409 and len(list((data / "secrets").iterdir())) == 1


# ---------- rescue (step 6) ----------

def snapshot(root):
    """Content files (not the skeleton) with their bytes and mode."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not p.is_symlink() and not arch._skeleton(rel):
            out[rel] = (p.read_bytes(), stat.S_IMODE(p.stat().st_mode) & 0o111 != 0)
    return out


def run_rescue(root, data, store, db, aid, password=PASS):
    name = oneshot.put(data / "secrets", password)
    rid = db.enqueue_maintenance("rescue", "ana", target=aid, secret=name)
    h = make_handlers(root, data, store=store, db=db)
    Runner(db, root, maintenance=h, secrets_dir=data / "secrets").run(db.claim_next(data / "logs"))
    return db.get(rid)


def no_staging(root):
    return not [p for p in root.iterdir() if p.name.startswith(".ccw-staging")]


def test_rescue_brings_everything_back(ws):
    root, data, store, db, jid = ws
    before = snapshot(root)
    before.pop("itemdb/notes/evil", None)
    aid = run_archive(root, data, store, db)["result"]["archive"]
    job = run_rescue(root, data, store, db, aid)
    assert job["status"] == "done", job
    assert job["result"] == {"archive": aid, "files": 7, "jobs": 1, "assumed": 1, "config": "same"}
    assert snapshot(root) == before                                       # same bytes, same exec bits
    assert not (root / "itemdb/notes/evil").exists()                      # the symlink was never archived
    assert db.marks()[("CC-0001", "4")]["reason"] == "management"
    assert jid in [j["id"] for j in db.list_jobs(None)] and db.list_jobs(None, audit=aid) == []
    assert (store / f"{aid}.tar.gz.gpg").exists() and no_staging(root)    # the archive is kept
    assert list((data / "secrets").iterdir()) == []


def test_rescue_with_wrong_password_changes_nothing(ws):
    root, data, store, db, _ = ws
    aid = run_archive(root, data, store, db)["result"]["archive"]
    empty = tree(root)
    job = run_rescue(root, data, store, db, aid, password=b"not-the-password")
    assert job["error"]["code"] == "bad_passphrase"
    assert tree(root) == empty and no_staging(root) and db.marks() == {}


def test_rescue_needs_an_empty_workspace(ws):
    root, data, store, db, _ = ws
    aid = run_archive(root, data, store, db)["result"]["archive"]
    (root / "src/new.c").write_text("new code")
    job = run_rescue(root, data, store, db, aid)
    assert job["error"]["code"] == "workspace_not_empty"
    assert (root / "src/new.c").read_text() == "new code" and not (root / "src/app.py").exists()


def test_rescue_brings_back_the_name_of_the_audit(ws):
    """A later upload renamed the project: rescuing the old audit restores ITS name."""
    root, data, store, db, _ = ws
    aid = run_archive(root, data, store, db)["result"]["archive"]
    (root / "codecome.yml").write_text('project:\n  name: "Otra auditoría"\n')
    job = run_rescue(root, data, store, db, aid)
    assert job["result"]["config"] == "same"
    assert "Demo App" in (root / "codecome.yml").read_text() and not (root / "codecome.yml.archived").exists()


def test_rescue_keeps_a_changed_codecome_yml(ws):
    root, data, store, db, _ = ws
    aid = run_archive(root, data, store, db)["result"]["archive"]
    (root / "codecome.yml").write_text("project:\n  name: Other\nagents:\n  recon: {model: x}\n")
    job = run_rescue(root, data, store, db, aid)
    assert job["result"]["config"] == "kept_current"
    cur = (root / "codecome.yml").read_text()
    assert "Demo App" in cur and "recon" in cur                          # name restored, config kept
    assert "Demo App" in (root / "codecome.yml.archived").read_text()


def fake_archive(store, aid, members, manifest=None):
    """Hand-made encrypted archive: lets the tests send what our own archiver never would."""
    import io
    p = store / f"{aid}.tar.gz.gpg"
    with gpgbox.encrypt_to(p, PASS) as w, tarfile.open(fileobj=w, mode="w|gz") as tf:
        man = {}
        for name, data, kind in members:
            ti = tarfile.TarInfo(name)
            if kind == "sym":
                ti.type, ti.linkname = tarfile.SYMTYPE, data
                tf.addfile(ti)
            else:
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
                man[name] = {"sha256": __import__("hashlib").sha256(data).hexdigest(), "size": len(data)}
        body = json.dumps(manifest if manifest is not None else man).encode()
        ti = tarfile.TarInfo("web/manifest.json")
        ti.size = len(body)
        tf.addfile(ti, io.BytesIO(body))
    (store / f"{aid}.json").write_text(json.dumps({"id": aid}))
    return aid


@pytest.mark.parametrize("members, manifest, code", [
    ([("workspace/src/a.c", b"int a;", "f")], {"workspace/src/a.c": {"sha256": "0" * 64, "size": 6}}, "archive_verify_failed"),
    ([("workspace/src/a.c", b"int a;", "f"), ("workspace/src/l", "/etc", "sym")], None, "extract_link"),
    ([("etc/cron.d/x", b"* * * * * root id", "f")], None, "extract_root_not_allowed"),
    ([("workspace/../../x", b"!", "f")], None, "extract_bad_name"),
])
def test_rescue_refuses_tampered_or_hostile_archives(ws, members, manifest, code):
    root, data, store, db, _ = ws
    assert run_archive(root, data, store, db)["status"] == "done"
    empty = tree(root)
    aid = fake_archive(store, "evil-20261127-120000", members, manifest)
    job = run_rescue(root, data, store, db, aid)
    assert job["error"]["code"] == code
    assert tree(root) == empty and no_staging(root)


def test_rescue_api(api, ws, monkeypatch):
    login, data = api
    root, _, store, db, _ = ws
    monkeypatch.setattr(main, "ARCHIVES_DIR", store)
    ana = login("ana")
    assert ana.post("/api/archives/missing-20261127-000000/rescue", json={"password": "x"}).status_code == 404
    aid = run_archive(root, data, store, db)["result"]["archive"]
    assert ana.get("/api/archives").json()["workspace_empty"] is True
    assert login("bob").post(f"/api/archives/{aid}/rescue", json={"password": "x"}).status_code == 403
    r = ana.post(f"/api/archives/{aid}/rescue", json={"password": "archive-pass-1234"})
    assert r.status_code == 201
    assert [p.read_bytes() for p in (data / "secrets").iterdir()] == [b"archive-pass-1234"]
    Runner(db, root, maintenance=make_handlers(root, data, store=store, db=db),
           secrets_dir=data / "secrets").run(db.claim_next(data / "logs"))
    assert ana.get("/api/archives").json()["workspace_empty"] is False
    r = ana.post(f"/api/archives/{aid}/rescue", json={"password": "archive-pass-1234"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "workspace_not_empty"


# ---------- empty without archiving ----------

def run_reset(root, data, db):
    rid = db.enqueue_maintenance("reset", "ana")
    Runner(db, root, maintenance=make_handlers(root, data, db=db)).run(db.claim_next(data / "logs"))
    return db.get(rid)


def test_reset_empties_without_an_archive(ws):
    root, data, store, db, jid = ws
    job = run_reset(root, data, db)
    assert job["status"] == "done", job
    assert job["result"]["project"] == "Demo App" and job["result"]["jobs"] == 1
    assert tree(root) == ["codecome.yml", "itemdb/", "itemdb/README.md", "itemdb/findings/", "itemdb/findings/PENDING/",
                          "itemdb/findings/PENDING/.gitkeep", "itemdb/notes/", "itemdb/notes/.gitkeep", "itemdb/reports/",
                          "itemdb/reports/.gitkeep", "runs/", "runs/.gitkeep", "src/", "src/.gitkeep"]
    assert list(store.iterdir()) == []                                    # nothing archived
    assert [j["action"] for j in db.list_jobs(None)] == ["reset"] and db.marks() == {}
    assert run_reset(root, data, db)["error"]["code"] == "nothing_to_reset"


def test_reset_stops_before_anything_if_a_dir_is_not_writable(ws):
    root, data, store, db, _ = ws
    os.chmod(root / "itemdb/codeql/runs", 0o555)
    try:
        job = run_reset(root, data, db)
    finally:
        os.chmod(root / "itemdb/codeql/runs", 0o755)
    assert job["error"]["code"] == "workspace_not_writable" and (root / "src/app.py").exists()


@pytest.mark.parametrize("user, body, status, code", [
    ("bob", {"confirm": "Demo App", "understood": True}, 403, "archive_forbidden"),
    ("ana", {"confirm": "Demo", "understood": True}, 400, "archive_confirm_mismatch"),
    ("ana", {"confirm": "Demo App"}, 400, "reset_not_confirmed"),
])
def test_reset_api_refusals(api, user, body, status, code):
    login, _ = api
    r = login(user).post("/api/reset", json=body)
    assert r.status_code == status and r.json()["detail"]["code"] == code


def test_reset_api_queues_and_refuses_when_empty(api, ws):
    login, data = api
    root, _, _, db, _ = ws
    ana = login("ana")
    assert ana.post("/api/reset", json={"confirm": "Demo App", "understood": True}).status_code == 201
    Runner(db, root, maintenance=make_handlers(root, data, db=db)).run(db.claim_next(data / "logs"))
    r = ana.post("/api/reset", json={"confirm": "Demo App", "understood": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "nothing_to_reset"
