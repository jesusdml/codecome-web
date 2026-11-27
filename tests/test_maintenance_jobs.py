# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""v1.1 step 2: maintenance jobs (archive / rescue / upload), their locks, the per-audit job scope
and the one-shot secrets that carry passwords from the web to the worker."""
import os
import stat
import time

import pytest
from fastapi.testclient import TestClient

from app import main, oneshot
from app.auth import Auth, hash_password
from app.gates import BLOCKING, launch_reasons
from app.itemdb import ItemDB
from app.jobs import DuplicateJob, InvalidJob, JobDB
from app.runner import MaintError, Runner
from app.safe_extract import ExtractError
from tests.test_api import FIX, PW
from tests.test_gates import Fake


@pytest.fixture()
def db(tmp_path):
    return JobDB(tmp_path / "jobs.db")


def phase(db, action="phase-1", status=None):
    jid = db.enqueue(action, None, "ana", ItemDB(FIX))
    if status:
        db.finish(jid, status, 0)
    return jid


# ---------- locks ----------

def test_maintenance_needs_nothing_active(db):
    jid = phase(db)
    with pytest.raises(DuplicateJob) as e:
        db.enqueue_maintenance("archive", "ana")
    assert e.value.code == "jobs_active" and e.value.params["id"] == jid
    db.finish(jid, "done", 0)
    assert db.enqueue_maintenance("archive", "ana") > jid


def test_no_phase_while_maintenance_and_no_two_maintenances(db):
    mid = db.enqueue_maintenance("upload", "ana", target="abc123")
    with pytest.raises(DuplicateJob) as e:
        phase(db)
    assert e.value.code == "maintenance_running" and e.value.params == {"id": mid, "action": "upload"}
    with pytest.raises(DuplicateJob):
        db.enqueue_maintenance("archive", "ana")


@pytest.mark.parametrize("action, target", [("phase-1", None), ("rm", None), ("rescue", "../x"), ("rescue", ""),
                                            ("rescue", "a b"), ("rescue", "x" * 200)])
def test_maintenance_validation(db, action, target):
    with pytest.raises(InvalidJob):
        db.enqueue_maintenance(action, "ana", target=target)


def test_running_maintenance_cannot_be_cancelled(db, tmp_path):
    db.enqueue_maintenance("archive", "ana")
    job = db.claim_next(tmp_path / "logs")
    assert job["status"] == "running"
    assert db.request_cancel(job["id"]) is False
    q = JobDB(tmp_path / "j2.db")
    qid = q.enqueue_maintenance("archive", "ana")
    assert q.request_cancel(qid) is True and q.get(qid)["status"] == "cancelled"


def test_launch_reasons_maintenance_blocks_first(tmp_path):
    jobs = [{"id": 9, "action": "rescue", "finding": None, "status": "running"}]
    r = launch_reasons("phase-1", None, root=tmp_path, jobs=jobs, itemdb=ItemDB(tmp_path), gates=Fake(True))
    assert r == [{"code": "maintenance_running", "params": {"id": 9, "action": "rescue"}}]
    assert "maintenance_running" in BLOCKING and "src_empty" in BLOCKING


@pytest.mark.real_src
def test_launch_needs_code_in_src(tmp_path):
    def reasons():
        return launch_reasons("phase-1", None, root=tmp_path, jobs=[], itemdb=ItemDB(tmp_path), gates=Fake(True))
    assert reasons() == [{"code": "src_empty", "params": {}}]           # missing
    (tmp_path / "src").mkdir()
    assert reasons() == [{"code": "src_empty", "params": {}}]           # empty
    (tmp_path / "src" / "main.c").write_text("int x;")
    assert reasons() == []
    other = tmp_path / "other"
    other.mkdir()
    (other / "a").write_text("x")
    os.rename(tmp_path / "src", tmp_path / "src.bak")
    os.symlink(other, tmp_path / "src")
    assert reasons() == [{"code": "src_empty", "params": {}}]           # a symlink is not our src/


# ---------- audit scope ----------

def test_archived_jobs_leave_the_current_audit(db):
    done = phase(db, "phase-1", "done")
    failed = phase(db, "phase-2", "failed")
    mid = db.enqueue_maintenance("archive", "ana")
    assert db.tag_audit("proj-20261127-120000") == 2
    assert [j["id"] for j in db.list_jobs(None)] == [mid]                # only the archive job itself
    assert {j["id"] for j in db.list_jobs(None, audit="proj-20261127-120000")} == {done, failed}
    assert db.untag_audit("proj-20261127-120000") == 2
    assert {j["id"] for j in db.list_jobs(None)} == {done, failed, mid}


def test_tag_never_moves_active_jobs(db):
    running = phase(db)
    assert db.tag_audit("a1") == 0
    assert [j["id"] for j in db.list_jobs(None)] == [running]


def test_marks_export_and_replace(db):
    db.set_mark("CC-0001", "4", "ana", "management")
    exported = db.export_marks()
    assert exported[0]["reason"] == "management"
    db.replace_marks([])
    assert db.marks() == {}
    db.replace_marks(exported)
    assert db.marks()[("CC-0001", "4")]["reason"] == "management"


def test_secret_name_never_leaves_the_server(db):
    jid = db.enqueue_maintenance("rescue", "ana", target="a1", secret="0" * 32)
    assert "secret" not in db.get(jid) and "secret" not in db.list_jobs()[0]
    assert db.take_secret_name(jid) == "0" * 32
    assert db.take_secret_name(jid) is None                              # cleared as read


# ---------- one-shot secrets ----------

def test_oneshot_roundtrip(tmp_path):
    d = tmp_path / "secrets"
    name = oneshot.put(d, b"correct horse battery")
    assert stat.S_IMODE((d / name).stat().st_mode) == 0o640
    buf = oneshot.take(d, name)
    assert bytes(buf) == b"correct horse battery" and not (d / name).exists()
    oneshot.wipe(buf)
    assert bytes(buf) == bytes(len(buf))
    with pytest.raises(oneshot.SecretError) as e:
        oneshot.take(d, name)
    assert e.value.code == "secret_missing"


@pytest.mark.parametrize("name", ["../x", "g" * 32, "A" * 32, "0" * 31, "", None])
def test_oneshot_bad_names(tmp_path, name):
    with pytest.raises(oneshot.SecretError) as e:
        oneshot.take(tmp_path, name)
    assert e.value.code == "secret_invalid"


def test_oneshot_refuses_symlink_and_sweeps_old(tmp_path):
    (tmp_path / "target").write_text("x")
    os.symlink(tmp_path / "target", tmp_path / ("1" * 32))
    with pytest.raises(oneshot.SecretError):
        oneshot.take(tmp_path, "1" * 32)
    old = oneshot.put(tmp_path, b"a")
    new = oneshot.put(tmp_path, b"b")
    os.utime(tmp_path / old, (time.time() - 7200,) * 2)
    assert oneshot.sweep(tmp_path) == 1
    assert not (tmp_path / old).exists() and (tmp_path / new).exists() and (tmp_path / "target").exists()
    with pytest.raises(oneshot.SecretError):
        oneshot.put(tmp_path, b"")


# ---------- the worker side ----------

def run_one(tmp_path, db, handler, *, secret=None, action="archive"):
    sdir = tmp_path / "secrets"
    name = oneshot.put(sdir, secret) if secret else None
    jid = db.enqueue_maintenance(action, "ana", secret=name)
    runner = Runner(db, tmp_path, maintenance={action: handler} if handler else {}, secrets_dir=sdir)
    status = runner.run(db.claim_next(tmp_path / "logs"))
    return status, db.get(jid), name, sdir


def test_handler_gets_the_secret_once_and_it_is_gone(tmp_path, db):
    seen = {}

    def handler(job, secret, log):
        seen["secret"] = bytes(secret)
        seen["buf"] = secret
        log("working on it")
        return {"archive": "p-1"}

    status, job, name, sdir = run_one(tmp_path, db, handler, secret=b"pw-123456789012")
    assert status == "done" and job["result"] == {"archive": "p-1"} and job["error"] is None
    assert seen["secret"] == b"pw-123456789012"
    assert bytes(seen["buf"]) == bytes(len(seen["buf"]))                  # wiped after the handler
    assert not (sdir / name).exists()
    log = (tmp_path / "logs" / f"{job['id']}.log").read_text()
    assert "working on it" in log and "pw-123456789012" not in log
    assert job["live"]["last_line"] == "done"


@pytest.mark.parametrize("exc, code", [(MaintError("archive_verify_failed", file="x"), "archive_verify_failed"),
                                       (ExtractError("link", "/etc"), "extract_link"),
                                       (RuntimeError("secret path /home/x"), "maintenance_failed")])
def test_handler_failures(tmp_path, db, exc, code):
    def handler(job, secret, log):
        raise exc

    status, job, name, sdir = run_one(tmp_path, db, handler, secret=b"pw-123456789012")
    assert status == "failed" and job["error"]["code"] == code
    assert not (sdir / name).exists()
    assert "/home/x" not in (tmp_path / "logs" / f"{job['id']}.log").read_text()


def test_missing_handler_and_missing_secret(tmp_path, db):
    status, job, *_ = run_one(tmp_path, db, None)
    assert status == "failed" and job["error"]["code"] == "maintenance_unavailable"

    db2 = JobDB(tmp_path / "j2.db")
    jid = db2.enqueue_maintenance("archive", "ana", secret="f" * 32)      # file never written
    Runner(db2, tmp_path, maintenance={"archive": lambda *a: {}}, secrets_dir=tmp_path / "s").run(
        db2.claim_next(tmp_path / "logs2"))
    assert db2.get(jid)["error"]["code"] == "secret_missing"


# ---------- API ----------

def test_cancelling_a_queued_maintenance_job_deletes_its_secret(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "auth", Auth({"ana": hash_password(PW)}, b"k" * 32))
    monkeypatch.setattr(main, "jobs", JobDB(tmp_path / "jobs.db"))
    monkeypatch.setattr(main, "SECRETS_DIR", tmp_path / "secrets")
    name = oneshot.put(tmp_path / "secrets", b"pw")
    jid = main.jobs.enqueue_maintenance("rescue", "ana", target="a1", secret=name)
    c = TestClient(main.app)
    assert c.post("/api/login", json={"username": "ana", "password": PW}).status_code == 200
    assert c.post(f"/api/jobs/{jid}/cancel").status_code == 200
    assert not (tmp_path / "secrets" / name).exists()
    j = c.get(f"/api/jobs/{jid}").json()["job"]
    assert j["status"] == "cancelled" and "secret" not in j
