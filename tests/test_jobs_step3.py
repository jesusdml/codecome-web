# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
import os
import time

from app import jobs as jobsmod
from app.monitor import TranscriptTail
from app.runner import Runner, produced_files
from tests.test_monitor import _write


def test_list_jobs_filters_and_no_cap(tmp_path):
    db = jobsmod.JobDB(tmp_path / "j.db")
    with db._conn() as c:
        for i in range(60):
            c.execute("INSERT INTO jobs(action,finding,status,requested_by,created_at) VALUES(?,?,?,?,?)",
                      ("phase-1" if i % 2 == 0 else "phase-4", None if i % 2 == 0 else f"CC-{i:04d}", "done", "u", 1.0))
    assert len(db.list_jobs()) == 50  # default unchanged
    assert len(db.list_jobs(None)) == 60
    assert len(db.list_jobs(None, action="phase-1")) == 30
    assert [j["finding"] for j in db.list_jobs(None, finding="CC-0003")] == ["CC-0003"]
    assert len(db.list_jobs(5, action="phase-4")) == 5
    # injection attempt is just a value that matches nothing
    assert db.list_jobs(None, action="x' OR '1'='1") == []


def test_migration_adds_produced_idempotently(tmp_path):
    import sqlite3
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.executescript("""CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, finding TEXT,
      status TEXT NOT NULL, requested_by TEXT NOT NULL, created_at REAL NOT NULL, started_at REAL, finished_at REAL,
      exit_code INTEGER, error TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0, log_path TEXT, live TEXT);
      INSERT INTO jobs(action,status,requested_by,created_at) VALUES('phase-1','done','u',1.0);""")
    c.commit(); c.close()
    for _ in range(2):  # opening twice must not fail
        db = jobsmod.JobDB(p)
    assert db.get(1)["produced"] is None  # old job: unknown, not "nothing"
    db.set_produced(1, [])
    assert db.get(1)["produced"] == []


def test_produced_files_window_and_kinds(tmp_path):
    def w(rel, mtime):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
        os.utime(f, (mtime, mtime))
    now = time.time()
    w("itemdb/notes/old.md", now - 100)
    w("itemdb/notes/new.md", now + 1)
    w("itemdb/findings/PENDING/CC-0001-a.md", now + 1)
    w("itemdb/evidence/CC-0001/README.md", now + 1)
    w("itemdb/reports/report.md", now + 1)
    w("runs/phase-2-summary.md", now + 1)
    w("src/app.py", now + 1)  # outside the watched trees
    (tmp_path / "itemdb/notes/link.md").symlink_to(tmp_path / "src/app.py")
    got = {x["path"]: x["kind"] for x in produced_files(tmp_path, now)}
    assert got == {"itemdb/notes/new.md": "note", "itemdb/findings/PENDING/CC-0001-a.md": "finding",
                   "itemdb/evidence/CC-0001/README.md": "evidence", "itemdb/reports/report.md": "report",
                   "runs/phase-2-summary.md": "summary"}


def test_runner_fills_produced_for_ok_and_failed(tmp_path):
    db = jobsmod.JobDB(tmp_path / "j.db")
    root = tmp_path / "root"
    (root / "itemdb/notes").mkdir(parents=True)

    def argv(action, finding):
        return ["python3", "-c", "import sys,pathlib;pathlib.Path('itemdb/notes/n.md').write_text('x');sys.exit(0 if '"
                + action + "'=='phase-1' else 3)"]
    r = Runner(db, root, argv_builder=argv, interval=0.05)
    for action, want in (("phase-1", "done"), ("phase-2", "failed")):
        with db._conn() as c:
            c.execute("INSERT INTO jobs(action,status,requested_by,created_at) VALUES(?,?,?,?)",
                      (action, "queued", "u", time.time()))
        job = db.claim_next(tmp_path / "logs")
        assert r.run(job) == want
        assert db.get(job["id"])["produced"] == [{"path": "itemdb/notes/n.md", "kind": "note"}]


def test_transcript_sums_usage_across_sessions_and_files(tmp_path):
    def upd(sid, i, o, cost):
        return {"type": "session.updated", "properties": {"info": {
            "id": sid, "tokens": {"input": i, "output": o, "cache": {"read": 1, "write": 0}}, "cost": cost}}}
    _write(tmp_path / "last-phase-1a-x-pid5.jsonl", upd("s1", 10, 1, 0.1), upd("s1", 30, 3, 0.3))
    _write(tmp_path / "last-phase-1b-x-pid5.jsonl", upd("s2", 100, 7, 0.5))
    t = TranscriptTail(tmp_path)
    t.poll({5}, now=1.0)
    s = t.snapshot(1.0)
    # s1 reports cumulative totals: only its LATEST (30) counts, then s2 adds up
    assert s["tokens"]["input"] == 130 and s["tokens"]["output"] == 10
    assert s["tokens"]["cache"]["read"] == 2
    assert round(s["cost"], 6) == 0.8


def test_api_filters_and_validation(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import main
    from app.auth import Auth, hash_password
    from app.itemdb import ItemDB
    from tests.test_api import FIX, PW
    monkeypatch.setattr(main, "db", ItemDB(FIX))
    monkeypatch.setattr(main, "auth", Auth({"ana": hash_password(PW)}, b"k" * 32))
    monkeypatch.setattr(main, "jobs", jobsmod.JobDB(tmp_path / "jobs.db"))
    c = TestClient(main.app)
    assert c.post("/api/login", json={"username": "ana", "password": PW}).status_code == 200
    with main.jobs._conn() as cx:
        for i in range(70):
            cx.execute("INSERT INTO jobs(action,status,requested_by,created_at) VALUES('phase-1','done','u',1.0)")
        cx.execute("INSERT INTO jobs(action,status,requested_by,created_at) VALUES('phase-2','done','u',1.0)")
    assert len(c.get("/api/jobs").json()["jobs"]) == 50
    assert len(c.get("/api/jobs?action=phase-1").json()["jobs"]) == 70  # filter: no 50 cap
    assert len(c.get("/api/jobs?action=phase-1&limit=3").json()["jobs"]) == 3
    assert c.get("/api/jobs?action=rm").status_code == 400
    assert c.get("/api/jobs?finding=CC-1;ls").status_code == 400
    assert c.get("/api/jobs?limit=0").status_code == 422
    assert "produced" in c.get("/api/jobs/1").json()["job"]
