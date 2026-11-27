# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main, phases
from app.audit_view import build_audit, build_phase
from app.auth import Auth, hash_password
from app.itemdb import ItemDB
from app.jobs import JobDB
from tests.conftest import PassingGates
from tests.test_api import PW
from tests.test_audit import complete, finding, job, phase1a, phase1b, phase1c, phase2, phase4, put
from tests.test_gates import Fake

APP = Path(__file__).resolve().parent.parent / "app"
EN = json.loads((APP / "locales" / "en.json").read_text(encoding="utf-8"))
PROFILES = {"default": "mock", "profiles": [{"name": "mock", "enabled": True}, {"name": "local", "enabled": True}]}
ALIVE = {"alive": True, "since": 1.0}

TOP = {"worker", "active", "queue", "project", "progress", "next", "open_questions", "phases", "matrix", "profiles"}
PHASE = {"key", "status", "scope", "last_run", "reference", "open_questions"}  # repo phases also have launch
LAUNCH = {"allowed", "reasons"}


def build(root, jobs=(), gates=None, worker=ALIVE, profiles=PROFILES, now=1000.0):
    return build_audit(root=root, jobs=list(jobs), itemdb=ItemDB(root), gates=gates or Fake(True),
                       worker=worker, profiles=profiles, now=now)


def scenarios(tmp_path):
    """The six workspaces of step 2."""
    out = {}
    for name, steps in {"empty": [], "after_1a": [phase1a], "after_1c": [phase1a, phase1b, phase1c],
                        "after_2": [phase1a, phase1b, phase1c, phase2],
                        "after_4": [phase1a, phase1b, phase1c, phase2, phase4], "complete": [complete]}.items():
        root = tmp_path / name
        root.mkdir()
        for s in steps:
            s(root)
        out[name] = root
    return out


def test_schema_over_the_six_fixtures(tmp_path):
    for name, root in scenarios(tmp_path).items():
        d = build(root)
        assert set(d) == TOP, name
        json.dumps(d)  # must be serializable as is
        assert [p["key"] for p in d["phases"]] == ["1", "2", "3", "4", "5", "6"]
        assert set(d["progress"]) == {"phases_done", "phases_total"} and d["progress"]["phases_total"] == 6
        assert set(d["profiles"]) == {"default", "list", "models"}
        for p in d["phases"]:
            assert PHASE <= set(p), (name, p["key"])
            assert p["scope"]["kind"] in ("repo", "finding")
            if p["scope"]["kind"] == "repo":
                assert set(p["launch"]) == LAUNCH
            else:  # 4 and 5 launch from matrix cells, one per finding
                assert "launch" not in p and "status_counts" in p
            for s in p.get("steps", []):
                assert {"key", "status", "artifacts", "launch"} <= set(s)
        for row in d["matrix"]:
            assert {"id", "title", "severity", "status", "p4", "p5"} <= set(row)
            for k in ("p4", "p5"):
                assert {"status", "launch", "job"} <= set(row[k]) and set(row[k]["launch"]) == LAUNCH
        assert "code" in d["next"] and "params" in d["next"]


def test_next_step_sequence(tmp_path):
    s = scenarios(tmp_path)
    assert build(s["empty"])["next"] == {"code": "launch_phase", "params": {"phase": "1"}}
    assert build(s["after_1c"])["next"] == {"code": "launch_phase", "params": {"phase": "2"}}
    assert build(s["after_2"])["next"] == {"code": "launch_phase", "params": {"phase": "3"}}
    put(s["after_2"], "runs/phase-3-summary.md")
    assert build(s["after_2"])["next"] == {"code": "validate_pending", "params": {"n": 3}}
    assert build(s["empty"], jobs=[job(5, "phase-1", "running")])["next"] == {"code": "running", "params": {"job": 5}}


def test_blocked_next_explains_why(tmp_path):
    d = build(scenarios(tmp_path)["empty"], gates=Fake(False, "no src"))
    assert d["next"]["code"] == "blocked" and d["next"]["params"]["reason"]["code"] == "gate_failed"


def test_launch_reasons_include_order_and_system_state(tmp_path):
    root = scenarios(tmp_path)["after_1a"]
    d = build(root, worker={"alive": False, "since": 99})
    by = {p["key"]: p for p in d["phases"]}
    codes = [r["code"] for r in by["2"]["launch"]["reasons"]]
    assert codes == ["needs_phase", "worker_down"] and by["2"]["launch"]["allowed"] is False
    assert by["1"]["launch"]["reasons"] == [{"code": "worker_down", "params": {}}]
    ok = build(root)
    assert {p["key"]: p["launch"]["allowed"] for p in ok["phases"] if "launch" in p}["1"] is True
    off = build(root, profiles={"default": "mock", "profiles": [{"name": "mock", "enabled": False}]})
    assert off["phases"][0]["launch"]["reasons"][0] == {"code": "profile_disabled", "params": {"name": "mock"}}


def test_matrix_cells_follow_finding_state(tmp_path):
    d = build(scenarios(tmp_path)["after_4"])
    rows = {r["id"]: r for r in d["matrix"]}
    assert rows["CC-0001"]["p4"]["launch"]["reasons"][0]["code"] == "finding_wrong_state"   # already CONFIRMED
    assert rows["CC-0001"]["p5"]["launch"]["allowed"] is True
    assert rows["CC-0002"]["p4"]["launch"]["allowed"] is True
    assert rows["CC-0002"]["p5"]["launch"]["reasons"][0]["code"] == "finding_wrong_state"


def test_active_queue_last_run_and_reference(tmp_path):
    root = scenarios(tmp_path)["after_1a"]
    jobs = [  # newest first, like list_jobs
        {"id": 4, "action": "phase-4", "finding": "CC-0001", "status": "queued"},
        {"id": 3, "action": "phase-1", "finding": None, "status": "running", "profile": "local", "started_at": 900.0,
         "finished_at": None, "live": {"agent": {"model": "p/m", "tokens": {"input": 5}, "cost": 0}}},
        {"id": 2, "action": "phase-1", "finding": None, "status": "done", "profile": "local", "started_at": 100.0, "finished_at": 160.0},
        {"id": 1, "action": "phase-1", "finding": None, "status": "done", "profile": "local", "started_at": 100.0, "finished_at": 300.0},
    ]
    d = build(root, jobs=jobs)
    run = d["active"].pop("run")
    assert d["active"] == {"job": 3, "phase": "1", "sub": "1b", "finding": None}
    assert run["job"] == 3 and run["status"] == "running" and "live" in run
    assert d["queue"] == [{"job": 4, "phase": "4", "finding": "CC-0001"}]
    p1 = d["phases"][0]
    assert p1["last_run"]["elapsed"] == 100.0 and p1["last_run"]["model"] == "p/m" and p1["last_run"]["tokens"] == {"input": 5}
    assert p1["reference"] == {"profile": "local", "median_elapsed": 130.0}   # median of 60 and 200
    assert p1["launch"]["reasons"] == [{"code": "job_active", "params": {"id": 3}}]


def test_build_phase(tmp_path):
    sc = scenarios(tmp_path)
    root = sc["after_1a"]
    jobs = [{"id": 1, "action": "phase-1", "finding": None, "status": "done", "requested_by": "ana", "created_at": 1.0,
             "produced": [{"path": "x", "kind": "note"}]},
            {"id": 2, "action": "phase-2", "finding": None, "status": "done", "requested_by": "ana", "created_at": 2.0}]
    d = build_phase("1a", root=root, jobs=jobs)
    assert d["key"] == "1a" and d["phase"] == "1" and [s["key"] for s in d["steps"]] == ["1a"]
    assert [r["job"] for r in d["runs"]] == [1] and d["runs"][0]["produced"] == 1
    paths = {a["path"]: a for a in d["artifacts"]}
    assert "itemdb/notes/target-profile.md" in paths and paths["itemdb/notes/target-profile.md"]["kind"] == "note"
    assert set(paths["itemdb/notes/target-profile.md"]) == {"path", "kind", "size", "mtime"}
    assert [s["key"] for s in build_phase("1", root=root, jobs=[])["steps"]] == ["1a", "1b", "1c"]
    four = build_phase("4", root=sc["after_4"], jobs=[])
    assert four["scope"] == {"kind": "finding"} and four["artifacts"] == []  # phase 4 is proven by finding state
    for bad in ("7", "1d", "../x", "", "4-5", "phase-1"):
        assert build_phase(bad, root=root, jobs=[]) is None


@pytest.fixture()
def api(monkeypatch, tmp_path):
    root = scenarios(tmp_path)["after_2"]
    monkeypatch.setattr(main, "CODECOME_ROOT", root)
    monkeypatch.setattr(main, "db", ItemDB(root))
    monkeypatch.setattr(main, "auth", Auth({"ana": hash_password(PW)}, b"k" * 32))
    monkeypatch.setattr(main, "jobs", JobDB(tmp_path / "jobs.db"))
    c = TestClient(main.app)
    return c


def test_endpoints_need_a_session(api):
    for url in ("/api/audit", "/api/phases/1", "/api/phases/1a"):
        assert api.get(url).status_code == 401


def test_endpoints_end_to_end(api):
    assert api.post("/api/login", json={"username": "ana", "password": PW}).status_code == 200
    d = api.get("/api/audit").json()
    assert set(d) == TOP and d["worker"]["alive"] is False
    assert d["phases"][1]["status"] == "done" and len(d["matrix"]) == 3
    assert api.get("/api/phases/2").json()["phase"] == "2"
    for bad in ("7", "1d", "phase-1"):
        r = api.get(f"/api/phases/{bad}")
        assert r.status_code == 404 and r.json()["detail"]["code"] == "phase_not_found"


# ---------- every code the server can emit has a text, in both languages ----------

def test_translations_cover_what_the_view_emits():
    src = (APP / "gates.py").read_text() + (APP / "audit_view.py").read_text()
    reason_codes = set(re.findall(r'"code": "(\w+)"', src))
    next_codes = {c for c in reason_codes if c in ("launch_phase", "validate_pending", "running", "blocked", "all_done")}
    reason_codes -= next_codes
    assert {"needs_phase", "gate_failed", "gate_unavailable", "job_active", "worker_down", "profile_disabled",
            "finding_wrong_state", "finding_not_found"} <= reason_codes
    need = {f"reason.{c}" for c in reason_codes} | {f"next.{c}" for c in next_codes | {"review_questions"}}
    need |= {f"reason.gate_unavailable.{r}" for r in ("missing", "timeout", "failed_to_run")}
    need |= {f"status.{s}" for s in ("none", "queued", "running", "done", "failed", "partial")}
    keys = [n.key for p in phases.PHASES for n in p.nodes] + ["1"]
    need |= {f"phase.{k}.{f}" for k in keys for f in ("title", "does", "needs", "produces", "repeat")}
    need |= {f"error.{c}.hint" for c in ("timeout", "exit_code", "worker_restarted", "profile_unavailable", "launch_failed")}
    for lang in ("en", "es"):
        d = json.loads((APP / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
        assert not sorted(need - set(d)), (lang, sorted(need - set(d)))


def test_stopped_worker_does_not_hide_the_next_step(tmp_path):
    d = build(scenarios(tmp_path)["empty"], worker={"alive": False, "since": None})
    assert d["next"] == {"code": "launch_phase", "params": {"phase": "1"}}


def test_queued_job_is_reported_as_the_next_thing(tmp_path):
    d = build(scenarios(tmp_path)["empty"], jobs=[job(3, "phase-1", "queued")])
    assert d["next"] == {"code": "running", "params": {"job": 3}}
