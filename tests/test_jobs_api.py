# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import Auth, hash_password
from app.itemdb import ItemDB
from app.jobs import JobDB
from tests.test_api import FIX, PW


@pytest.fixture()
def c(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "db", ItemDB(FIX))
    monkeypatch.setattr(main, "auth", Auth({"ana": hash_password(PW)}, b"k" * 32))
    monkeypatch.setattr(main, "jobs", JobDB(tmp_path / "jobs.db"))
    monkeypatch.setattr(main, "LOG_DIR", tmp_path / "logs")
    (tmp_path / "logs").mkdir()
    cl = TestClient(main.app)
    assert cl.post("/api/login", json={"username": "ana", "password": PW}).status_code == 200
    return cl


def test_jobs_require_login(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "auth", Auth({"ana": "x"}, b"k" * 32))
    monkeypatch.setattr(main, "jobs", JobDB(tmp_path / "j.db"))
    cl = TestClient(main.app)
    assert cl.get("/api/jobs").status_code == 401
    assert cl.post("/api/jobs", json={"action": "phase-1"}).status_code == 401
    assert cl.get("/api/jobs/1/log").status_code == 401


def test_create_list_get(c):
    r = c.post("/api/jobs", json={"action": "phase-1"})
    assert r.status_code == 201
    jid = r.json()["id"]
    d = c.get("/api/jobs").json()
    assert d["jobs"][0]["id"] == jid and d["jobs"][0]["requested_by"] == "ana"
    assert "phase-6" in d["actions"] and d["worker"]["alive"] is False
    assert c.get(f"/api/jobs/{jid}").json()["job"]["status"] == "queued"
    assert c.get("/api/jobs/999").status_code == 404


@pytest.mark.parametrize("body", [
    {"action": "rm -rf /"}, {"action": "phase-9"}, {"action": "phase-4"},
    {"action": "phase-4", "finding": "CC-1;ls"}, {"action": "phase-4", "finding": "CC-9999"},
    {"action": "phase-5", "finding": "CC-0001"}, {"action": "phase-1", "finding": "CC-0001"},
])
def test_create_rejects_bad_requests(c, body):
    assert c.post("/api/jobs", json=body).status_code == 400


def test_create_finding_phase_ok_and_duplicate(c):
    assert c.post("/api/jobs", json={"action": "phase-4", "finding": "CC-0001"}).status_code == 201
    assert c.post("/api/jobs", json={"action": "phase-4", "finding": "CC-0001"}).status_code == 409


def test_cross_origin_post_blocked(c):
    r = c.post("/api/jobs", json={"action": "phase-1"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    r = c.post("/api/jobs", json={"action": "phase-1"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 201


def test_form_post_rejected(c):
    r = c.post("/api/jobs", data="action=phase-1", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 422


def test_cancel(c):
    jid = c.post("/api/jobs", json={"action": "phase-1"}).json()["id"]
    assert c.post(f"/api/jobs/{jid}/cancel").status_code == 200
    assert c.get(f"/api/jobs/{jid}").json()["job"]["status"] == "cancelled"
    assert c.post(f"/api/jobs/{jid}/cancel").status_code == 409
    assert c.post(f"/api/jobs/{jid}/cancel", headers={"Origin": "http://evil.example"}).status_code == 403


def _finished_job(c, tmp_path, text: bytes):
    jid = c.post("/api/jobs", json={"action": "phase-1"}).json()["id"]
    job = main.jobs.claim_next(tmp_path / "logs")
    (tmp_path / "logs" / f"{jid}.log").write_bytes(text)
    main.jobs.finish(job["id"], "done", 0)
    return jid


def _events(resp):
    out, ev = [], {}
    for line in resp.iter_lines():
        if line == "":
            if ev:
                out.append(ev)
            ev = {}
        elif ":" in line:
            k, _, v = line.partition(": ")
            ev[k] = v
    return out


def test_log_stream_replays_finished_job_and_ends(c, tmp_path):
    import json
    jid = _finished_job(c, tmp_path, b"hola\n\x1b[31mrojo\x1b[0m\n\xc3\xa1\n")
    with c.stream("GET", f"/api/jobs/{jid}/log") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        evs = _events(r)
    text = "".join(json.loads(e["data"]) for e in evs if e.get("event") == "log")
    assert text == "hola\nrojo\ná\n"  # ANSI removed, UTF-8 intact
    assert evs[-1]["event"] == "end"


def test_log_stream_resumes_from_last_event_id(c, tmp_path):
    import json
    jid = _finished_job(c, tmp_path, b"0123456789")
    with c.stream("GET", f"/api/jobs/{jid}/log", headers={"Last-Event-ID": "6"}) as r:
        evs = _events(r)
    assert json.loads(evs[0]["data"]) == "6789"


def test_log_outside_log_dir_is_never_served(c, tmp_path):
    secret = tmp_path / "secreto.txt"
    secret.write_text("NO-SERVIR")
    jid = c.post("/api/jobs", json={"action": "phase-1"}).json()["id"]
    job = main.jobs.claim_next(tmp_path / "logs")
    with main.jobs._conn() as cx:
        cx.execute("UPDATE jobs SET log_path=? WHERE id=?", (str(secret), jid))
    main.jobs.finish(job["id"], "done", 0)
    with c.stream("GET", f"/api/jobs/{jid}/log") as r:
        body = "".join(r.iter_lines())
    assert "NO-SERVIR" not in body and "event: end" in body


def test_log_unknown_job_404(c):
    assert c.get("/api/jobs/999/log").status_code == 404
