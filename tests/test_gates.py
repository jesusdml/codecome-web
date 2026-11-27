# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
import os
import sys
import textwrap

import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import Auth, hash_password
from app.gates import BLOCKING, GateResult, GateRunner, clean, launch_reasons
from app.itemdb import ItemDB
from app.jobs import JobDB
from tests.test_api import FIX, PW
from tests.test_audit import finding, phase1a, phase1b, phase1c, put


def fake_root(tmp_path, body="sys.exit(0)"):
    root = tmp_path / "ws"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "gate-check.py").write_text("import sys, os\n" + textwrap.dedent(body))
    return root


class Fake:
    """Injected gate: no subprocess."""
    def __init__(self, ok=True, detail=""):
        self.res, self.calls = GateResult(ok, detail), []

    def check(self, key, finding=None, max_age=None):
        self.calls.append((key, finding))
        return self.res


# ---------- the runner ----------

def test_runner_uses_arg_list_no_shell_and_minimal_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    root = fake_root(tmp_path, '''
        print("ARGS", sys.argv[1:], "NO_COLOR", os.environ.get("NO_COLOR"), "KEY", os.environ.get("ANTHROPIC_API_KEY"))
        sys.exit(0)''')
    r = GateRunner(root, ttl=0).check("4", "CC-0001")
    assert r.ok and "ARGS ['4', 'CC-0001']" in r.detail and "NO_COLOR 1" in r.detail and "KEY None" in r.detail


def test_exit_code_is_the_verdict_and_output_is_cleaned(tmp_path):
    root = fake_root(tmp_path, r'''
        print("\x1b[31m[FAIL]\x1b[0m <b>no</b>\x00\x07 sandbox")
        sys.exit(1)''')
    r = GateRunner(root, ttl=0).check("2")
    assert r.ok is False and r.error is None
    assert "\x1b" not in r.detail and "\x00" not in r.detail and "[FAIL]" in r.detail
    assert len(clean("x" * 10000)) == 4000


def test_timeout_kills_the_whole_group(tmp_path):
    root = fake_root(tmp_path, '''
        import subprocess, time
        c = subprocess.Popen(["sleep", "600"])
        time.sleep(600)''')
    t0 = __import__("time").time()
    r = GateRunner(root, timeout=0.5, ttl=0).check("1")
    assert r.error == "timeout" and r.ok is False and __import__("time").time() - t0 < 5


def test_missing_script_and_cache(tmp_path):
    assert GateRunner(tmp_path).check("1").error == "missing"
    calls, now = [], [0.0]
    def ex(argv, cwd, timeout):
        calls.append(argv)
        return 0, "ok"
    root = fake_root(tmp_path)
    g = GateRunner(root, execute=ex, ttl=2.0, clock=lambda: now[0])
    g.check("1"); g.check("1"); g.check("1", None)
    assert len(calls) == 1          # short cache
    now[0] = 3.0
    g.check("1")
    assert len(calls) == 2          # expired
    g.check("4", "CC-0001")
    assert len(calls) == 3          # different key


# ---------- reasons ----------

def reasons(action, finding, root, jobs=(), itemdb=None, gates=None):
    return launch_reasons(action, finding, root=root, jobs=list(jobs), itemdb=itemdb or ItemDB(root),
                          gates=gates or Fake())


def test_phase1_goes_straight_to_the_gate(tmp_path):
    g = Fake(False, "[FAIL] src/ is empty")
    r = reasons("phase-1", None, tmp_path, gates=g)
    assert r == [{"code": "gate_failed", "params": {"phase": "1", "detail": "[FAIL] src/ is empty"}}]
    assert reasons("phase-1", None, tmp_path, gates=Fake(True)) == []


def test_needs_phase_blocks_before_running_the_gate(tmp_path):
    phase1a(tmp_path)
    g = Fake()
    assert reasons("phase-2", None, tmp_path, gates=g) == [{"code": "needs_phase", "params": {"phase": "1c"}}]
    assert g.calls == []  # the structural reason already explains it
    phase1b(tmp_path); phase1c(tmp_path)
    assert reasons("phase-2", None, tmp_path, gates=g) == [] and g.calls == [("2", None)]


def test_gate_unavailable_fails_closed(tmp_path):
    r = reasons("phase-1", None, tmp_path, gates=GateRunner(tmp_path))
    assert r[0]["code"] == "gate_unavailable" and r[0]["params"]["reason"] == "missing"


def test_finding_reasons_and_job_active(tmp_path):
    finding(tmp_path, "PENDING", "CC-0001")
    finding(tmp_path, "CONFIRMED", "CC-0002")
    g = Fake()
    assert reasons("phase-4", "CC-0001", tmp_path, gates=g) == [] and g.calls == [("4", "CC-0001")]
    assert reasons("phase-4", "CC-0002", tmp_path, gates=g)[0] == {
        "code": "finding_wrong_state", "params": {"action": "phase-4", "needs": "PENDING"}}
    assert reasons("phase-5", "CC-0009", tmp_path, gates=g)[0]["code"] == "finding_not_found"
    busy = [{"id": 7, "action": "phase-4", "finding": "CC-0001", "status": "running"}]
    assert reasons("phase-4", "CC-0001", tmp_path, jobs=busy, gates=g)[0] == {"code": "job_active", "params": {"id": 7}}
    assert len(g.calls) == 1  # no more gate runs once something else explained the block


def test_no_phase_is_stacked_on_a_finding_with_an_active_job(tmp_path):
    # CodeCome promotes the finding to CONFIRMED before the phase-4 job ends: phase 5 must wait.
    finding(tmp_path, "CONFIRMED", "CC-0001")
    g = Fake()
    busy = [{"id": 16, "action": "phase-4", "finding": "CC-0001", "status": "running"}]
    r = reasons("phase-5", "CC-0001", tmp_path, jobs=busy, gates=g)
    assert r == [{"code": "finding_busy", "params": {"id": 16, "action": "phase-4"}}] and g.calls == []
    assert "finding_busy" in BLOCKING
    done = [dict(busy[0], status="done")]
    assert reasons("phase-5", "CC-0001", tmp_path, jobs=done, gates=g) == []


# ---------- API ----------

@pytest.fixture()
def api(monkeypatch, tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(main, "CODECOME_ROOT", root)
    monkeypatch.setattr(main, "db", ItemDB(root))
    monkeypatch.setattr(main, "auth", Auth({"ana": hash_password(PW)}, b"k" * 32))
    monkeypatch.setattr(main, "jobs", JobDB(tmp_path / "jobs.db"))
    c = TestClient(main.app)
    assert c.post("/api/login", json={"username": "ana", "password": PW}).status_code == 200
    return c, root


def test_post_blocked_by_gate_returns_409_with_reasons_and_enqueues_nothing(api, monkeypatch):
    c, root = api
    monkeypatch.setattr(main, "gates", Fake(False, "[FAIL] no sources"))
    r = c.post("/api/jobs", json={"action": "phase-1"})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "launch_blocked"
    assert d["params"]["reasons"] == [{"code": "gate_failed", "params": {"phase": "1", "detail": "[FAIL] no sources"}}]
    assert main.jobs.list_jobs(None) == []


def test_post_passes_when_the_gate_passes(api, monkeypatch):
    c, _ = api
    monkeypatch.setattr(main, "gates", Fake(True))
    assert c.post("/api/jobs", json={"action": "phase-1"}).status_code == 201


def test_post_needs_phase_blocks_in_both_directions(api, monkeypatch):
    c, root = api
    monkeypatch.setattr(main, "gates", Fake(True))
    r = c.post("/api/jobs", json={"action": "phase-2"})
    assert r.status_code == 409 and r.json()["detail"]["params"]["reasons"][0]["code"] == "needs_phase"
    phase1a(root); phase1b(root); phase1c(root)
    assert c.post("/api/jobs", json={"action": "phase-2"}).status_code == 201


def test_existing_errors_keep_their_codes(api, monkeypatch):
    c, root = api
    gates = Fake(False, "x")  # a failing gate must not mask these
    monkeypatch.setattr(main, "gates", gates)
    finding(root, "CONFIRMED", "CC-0002")
    assert c.post("/api/jobs", json={"action": "phase-4", "finding": "CC-1;ls"}).status_code == 400
    assert c.post("/api/jobs", json={"action": "rm -rf /"}).status_code == 400
    r = c.post("/api/jobs", json={"action": "phase-4", "finding": "CC-0002"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "finding_wrong_state"
    assert gates.calls == []
    monkeypatch.setattr(main, "gates", Fake(True))
    assert c.post("/api/jobs", json={"action": "phase-1"}).status_code == 201
    r = c.post("/api/jobs", json={"action": "phase-1"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "duplicate_job"


def test_post_without_session_is_401(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "auth", Auth({"ana": "x"}, b"k" * 32))
    assert TestClient(main.app).post("/api/jobs", json={"action": "phase-1"}).status_code == 401
