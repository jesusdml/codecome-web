# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""'Assume': accept a finding without validating (4) or exploiting (5) it, with a written reason.
A web-side decision stored in jobs.db; CodeCome's itemdb/ is never touched."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import audit, main
from app.auth import Auth, hash_password
from app.itemdb import ItemDB
from app.jobs import JobDB
from tests.test_api import PW
from tests.test_audit import finding, st


def marks_for(db):
    return db.marks()


def test_marks_store_reason_and_undo(tmp_path):
    db = JobDB(tmp_path / "jobs.db")
    db.set_mark("CC-0001", "4", "ana", "Needs hardware we do not have.")
    m = db.marks()[("CC-0001", "4")]
    assert m["by"] == "ana" and m["reason"] == "Needs hardware we do not have." and m["mark"] == "assumed"
    assert db.clear_mark("CC-0001", "4") is True and db.marks() == {}
    assert db.clear_mark("CC-0001", "4") is False


def test_old_database_gets_the_reason_column(tmp_path):
    p = tmp_path / "jobs.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE finding_mark (finding TEXT NOT NULL, phase TEXT NOT NULL, mark TEXT NOT NULL,"
              " by TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (finding, phase))")
    c.commit(); c.close()
    db = JobDB(p)
    db.set_mark("CC-0001", "5", "ana", "why")
    assert db.marks()[("CC-0001", "5")]["reason"] == "why"


def test_assumed_cells_settle_phases_4_and_5(tmp_path):
    finding(tmp_path, "PENDING", "CC-0001")
    finding(tmp_path, "PENDING", "CC-0002")
    d = audit.derive(tmp_path, [])
    assert st(d)["4"] == "none"
    marks = {("CC-0001", "4"): {"mark": "assumed", "by": "ana", "at": 1, "reason": "r"}}
    d = audit.derive(tmp_path, [], marks)
    row = next(r for r in d["matrix"] if r["id"] == "CC-0001")
    assert row["p4"]["status"] == "assumed" and row["p4"]["mark"]["reason"] == "r"
    assert row["p5"]["status"] == "na"                       # not validated -> exploit does not apply
    assert st(d)["4"] == "partial"
    marks[("CC-0002", "4")] = {"mark": "assumed", "by": "ana", "at": 1, "reason": "r2"}
    d = audit.derive(tmp_path, [], marks)
    assert st(d)["4"] == "done" and st(d)["5"] == "done"     # everything settled, nothing proven


def test_a_running_job_wins_over_an_old_mark(tmp_path):
    finding(tmp_path, "PENDING", "CC-0001")
    marks = {("CC-0001", "4"): {"mark": "assumed", "by": "ana", "at": 1, "reason": "r"}}
    job = {"id": 3, "action": "phase-4", "finding": "CC-0001", "status": "running", "created_at": 1,
           "started_at": 1, "finished_at": None, "error": None, "live": None, "profile": None}
    d = audit.derive(tmp_path, [job], marks)
    assert d["matrix"][0]["p4"]["status"] == "running"


@pytest.fixture()
def api(monkeypatch, tmp_path):
    root = tmp_path / "ws"
    finding(root, "PENDING", "CC-0001")
    finding(root, "CONFIRMED", "CC-0002", validation="CONFIRMED")
    monkeypatch.setattr(main, "db", ItemDB(root))
    monkeypatch.setattr(main, "jobs", JobDB(tmp_path / "jobs.db"))
    monkeypatch.setattr(main, "auth", Auth({"ana": hash_password(PW)}, b"k" * 32))
    c = TestClient(main.app)
    return c


def test_api_assume_and_undo(api):
    assert api.post("/api/findings/CC-0001/assume", json={"phase": "4", "reason": "x"}).status_code == 401
    assert api.post("/api/login", json={"username": "ana", "password": PW}).status_code == 200
    r = api.post("/api/findings/CC-0001/assume", json={"phase": "4", "reason": "  Out of scope.\x07 "})
    assert r.status_code == 200
    assert main.jobs.marks()[("CC-0001", "4")]["reason"] == "Out of scope."   # control chars dropped, trimmed
    assert api.get("/api/findings/CC-0001").json()["marks"]["4"]["by"] == "ana"
    assert api.delete("/api/findings/CC-0001/assume/4").json() == {"ok": True}
    assert main.jobs.marks() == {}


@pytest.mark.parametrize("fid, body, status, code", [
    ("CC-0001", {"phase": "4", "reason": ""}, 422, None),                    # pydantic: min_length
    ("CC-0001", {"phase": "4", "reason": "\x07\x08"}, 400, "reason_required"),
    ("CC-0001", {"phase": "9", "reason": "x"}, 400, "invalid_phase"),
    ("CC-0001", {"phase": "5", "reason": "x"}, 409, "finding_wrong_state"),  # not confirmed yet
    ("CC-0002", {"phase": "4", "reason": "x"}, 409, "finding_wrong_state"),  # already confirmed
    ("CC-0099", {"phase": "4", "reason": "x"}, 404, "finding_not_found"),
    ("bad", {"phase": "4", "reason": "x"}, 400, "invalid_finding_id"),
    ("CC-0001", {"phase": "4", "reason": "x" * 2001}, 422, None),
])
def test_api_rejects_bad_assumptions(api, fid, body, status, code):
    api.post("/api/login", json={"username": "ana", "password": PW})
    r = api.post(f"/api/findings/{fid}/assume", json=body)
    assert r.status_code == status
    if code:
        assert r.json()["detail"]["code"] == code
    assert main.jobs.marks() == {}


def test_api_refuses_while_a_job_works_on_the_finding(api):
    api.post("/api/login", json={"username": "ana", "password": PW})
    jid = main.jobs.enqueue("phase-5", "CC-0002", "ana", main.db)
    r = api.post("/api/findings/CC-0002/assume", json={"phase": "5", "reason": "x"})
    assert r.status_code == 409 and r.json()["detail"] == {"code": "finding_busy", "params": {"id": jid}}


def test_api_assume_needs_same_origin(api):
    api.post("/api/login", json={"username": "ana", "password": PW})
    r = api.post("/api/findings/CC-0001/assume", json={"phase": "4", "reason": "x"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and main.jobs.marks() == {}
