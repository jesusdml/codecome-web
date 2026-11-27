# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from app.itemdb import ItemDB
from app.jobs import JobDB
from app.runner import Runner, clean_env, work_loop
from tests.test_api import FIX

FAKE = str(Path(__file__).parent / "fake_phase.py")


def setup(tmp_path, mode, extra=(), **kw):
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    db = JobDB(tmp_path / "data" / "jobs.db")
    builder = lambda action, finding: [sys.executable, FAKE, mode, *extra]  # noqa: E731
    runner = Runner(db, root, argv_builder=builder, interval=0.1, grace=1.0, **kw)
    jid = db.enqueue("phase-1", None, "ana", ItemDB(FIX))
    job = db.claim_next(tmp_path / "data" / "logs")
    return db, runner, jid, job


def test_ok_job_reports_real_signals(tmp_path):
    db, runner, jid, job = setup(tmp_path, "ok")
    assert runner.run(job) == "done"
    j = db.get(jid)
    assert j["status"] == "done" and j["exit_code"] == 0
    live = j["live"]
    assert live["state"] == "finished"
    assert live["agent"]["tokens"]["output"] == 1132
    assert live["agent"]["model"] == "local-ollama/qwen3.5:9b"
    assert live["agent"]["name"] == "recon" and live["agent"]["events"] > 30
    assert live["last_line"] == "phase end" and live["log_bytes"] > 0
    assert max(live["cpu_hist"]) > 20  # CPU real medida en /proc
    assert "phase end" in Path(j["log_path"]).read_text()


def test_failing_job(tmp_path):
    db, runner, jid, job = setup(tmp_path, "fail")
    assert runner.run(job) == "failed"
    j = db.get(jid)
    assert j["exit_code"] == 3 and j["error"] == {"code": "exit_code", "params": {"code": 3}}


def test_retry_state_is_visible_while_running(tmp_path):
    db, runner, jid, job = setup(tmp_path, "retry")
    seen = set()
    t = threading.Thread(target=runner.run, args=(job,))
    t.start()
    while t.is_alive():
        lv = (db.get(jid) or {}).get("live")
        if lv:
            seen.add(lv["state"])
        time.sleep(0.05)
    assert "retrying" in seen


def test_cancel_kills_the_whole_group(tmp_path):
    pidfile = tmp_path / "child.pid"
    db, runner, jid, job = setup(tmp_path, "fork", extra=(str(pidfile),))
    t = threading.Thread(target=runner.run, args=(job,))
    t.start()
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text():
            break
        time.sleep(0.05)
    child = int(pidfile.read_text())
    assert db.request_cancel(jid)
    t.join(10)
    assert not t.is_alive() and db.get(jid)["status"] == "cancelled"
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)  # el hijo tampoco sobrevive


def test_timeout(tmp_path):
    db, runner, jid, job = setup(tmp_path, "hang", timeout=0.5)
    t0 = time.time()
    assert runner.run(job) == "failed"
    assert time.time() - t0 < 8 and db.get(jid)["error"]["code"] == "timeout"


def test_silent_hang_becomes_stalled(tmp_path):
    db, runner, jid, job = setup(tmp_path, "hang", stall_s=0.5)
    t = threading.Thread(target=runner.run, args=(job,))
    t.start()
    state = None
    for _ in range(60):
        lv = (db.get(jid) or {}).get("live")
        state = lv and lv["state"]
        if state == "stalled":
            break
        time.sleep(0.1)
    db.request_cancel(jid)
    t.join(10)
    assert state == "stalled"


def test_launch_failure_is_reported(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    db = JobDB(tmp_path / "jobs.db")
    runner = Runner(db, root, argv_builder=lambda a, f: ["/no/existe"], interval=0.1)
    jid = db.enqueue("phase-1", None, "ana", ItemDB(FIX))
    assert runner.run(db.claim_next(tmp_path / "logs")) == "failed"
    assert db.get(jid)["error"]["code"] == "launch_failed"


def test_clean_env_drops_secrets_not_allowed():
    env = clean_env({"PATH": "/bin", "HOME": "/h", "ANTHROPIC_API_KEY": "k", "AWS_SECRET": "x",
                     "CODECOME_MODEL": "m", "SSH_AUTH_SOCK": "/s"})
    assert set(env) >= {"PATH", "HOME", "ANTHROPIC_API_KEY", "CODECOME_MODEL"}
    assert "AWS_SECRET" not in env and "SSH_AUTH_SOCK" not in env


def test_work_loop_runs_queue_then_stops(tmp_path):
    db, runner, jid, job = setup(tmp_path, "ok")
    db.finish(jid, "cancelled", None)  # release the claimed job
    a = db.enqueue("phase-2", None, "ana", ItemDB(FIX))
    b = db.enqueue("phase-3", None, "ana", ItemDB(FIX))
    done = []
    def stop():
        done.append(1)
        return all(db.get(i)["status"] == "done" for i in (a, b))
    work_loop(db, runner, tmp_path / "data" / "logs", stop, idle_sleep=0.05)
    assert db.get(a)["status"] == "done" and db.get(b)["status"] == "done"
    assert db.get(a)["started_at"] <= db.get(b)["started_at"]


def test_console_heartbeats_do_not_hide_a_stalled_job(tmp_path):
    """The server beats every 0.2 s but the job does not move: it must show as stalled."""
    db, runner, jid, job = setup(tmp_path, "beats", stall_s=1.0)
    t = threading.Thread(target=runner.run, args=(job,))
    t.start()
    state, beats = None, None
    for _ in range(80):
        lv = (db.get(jid) or {}).get("live")
        if lv:
            state, beats = lv["state"], lv["beats"]
            if state == "stalled":
                break
        time.sleep(0.1)
    db.request_cancel(jid)
    t.join(10)
    assert state == "stalled"
    assert beats["count"] >= 3 and beats["last_age"] < 2  # and still the heartbeats are visible
