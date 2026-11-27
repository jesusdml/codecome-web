# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
import os
import stat
import threading
import time

import pytest

from app.ledger import Ledger
from tests.test_runner import setup

DAY1 = 1_700_000_000.0          # 2023-11-14
DAY2 = DAY1 + 40 * 86400


def step(pid, ts=DAY1, model="claude-x", provider="anthropic", i=1_000_000, o=0, agent="recon", phase="2", cost=0):
    return {"part_id": pid, "session_id": "s", "ts": ts, "provider": provider, "model": model, "phase": phase,
            "agent": agent, "cost": cost, "tokens": {"input": i, "output": o, "reasoning": 0, "cache": {"read": 0, "write": 0}}}


def rec(led, steps, job=1, profile="local", user="ana", project="p", finding=None):
    return led.record(project=project, user=user, job_id=job, finding=finding, profile=profile,
                      steps=steps, fallback_phase="2")


def prices(tmp_path, rows):
    import json
    f = tmp_path / "prices.json"
    f.write_text(json.dumps({"models": {"claude-x": rows}}))
    return f


@pytest.fixture()
def led(tmp_path):
    return Ledger(tmp_path / "data" / "ledger.db")


def test_ingest_is_idempotent(led):
    assert rec(led, [step("a"), step("b")]) == 2
    assert rec(led, [step("a"), step("b")]) == 0  # same transcript again: nothing duplicated
    assert led.usage()["total"]["steps"] == 2
    assert rec(led, [step("a")], job=2) == 1       # same part id in ANOTHER job is a different step


def test_price_validity_dates_and_retroactive_fix(led, tmp_path):
    led.load_prices(prices(tmp_path, [{"valid_from": "2023-01-01", "input": 3, "output": 15, "cache_read": 0, "cache_write": 0},
                                      {"valid_from": "2024-01-01", "input": 5, "output": 15, "cache_read": 0, "cache_write": 0}]))
    rec(led, [step("a", ts=DAY1), step("b", ts=DAY2)], profile="api")
    u = led.usage(group="day")
    assert {g["key"]: round(g["cost"], 6) for g in u["groups"]} == {"2023-11-14": 3.0, "2023-12-24": 3.0}
    # price corrected for the first period: the past is recalculated without rewriting any step
    led.load_prices(prices(tmp_path, [{"valid_from": "2023-01-01", "input": 4, "output": 15, "cache_read": 0, "cache_write": 0}]))
    assert round(led.usage()["total"]["cost"], 6) == 8.0
    assert led.usage(price_as="nope")["total"]["unpriced_steps"] == 2


def test_mock_is_excluded_by_default(led):
    rec(led, [step("a", provider="test", model="mockmodel")], profile="mock")
    rec(led, [step("b")], job=2, profile="api")
    assert led.usage()["total"]["steps"] == 1
    assert led.usage(include_synthetic=True)["total"]["steps"] == 2


def test_local_counts_tokens_with_zero_cost_and_what_if(led, tmp_path):
    led.load_prices(prices(tmp_path, [{"valid_from": "2023-01-01", "input": 3, "output": 0, "cache_read": 0, "cache_write": 0}]))
    rec(led, [step("a", provider="local-ollama", model="qwen")])
    u = led.usage()
    assert u["total"]["tokens"]["input"] == 1_000_000 and u["total"]["cost"] == 0 and u["total"]["local_steps"] == 1
    assert led.usage(real=True)["total"]["steps"] == 0                      # not "real spend"
    assert round(led.usage(price_as="claude-x")["total"]["cost"], 6) == 3.0  # what it would have cost on the API


def test_retries_and_compactions_count_and_phases_sum(led):
    rec(led, [step("a", phase="1a"), step("b", phase="1a", agent="compaction")], job=1)
    rec(led, [step("a", phase="1a")], job=2, profile="api")  # a retry (another job) also spent tokens
    u = led.usage(group="phase")
    g = {x["key"]: x for x in u["groups"]}
    assert g["1a"]["steps"] == 3 and g["1a"]["compaction_steps"] == 1
    assert u["total"]["tokens"]["input"] == sum(x["tokens"]["input"] for x in u["groups"])


def test_project_filter_and_groups(led):
    rec(led, [step("a")], project="p1", user="ana")
    rec(led, [step("a")], job=2, project="p2", user="bob")
    assert led.usage(project="p1")["total"]["steps"] == 1
    assert {g["key"] for g in led.usage(group="user")["groups"]} == {"ana", "bob"}
    assert led.usage(since=DAY1 + 1)["total"]["steps"] == 0
    with pytest.raises(ValueError):
        led.usage(group="1; DROP TABLE usage_step")


def test_archiving_a_workspace_does_not_touch_the_ledger(led, tmp_path):
    ws = tmp_path / "ws" / "itemdb"
    ws.mkdir(parents=True)
    rec(led, [step("a")])
    import shutil
    shutil.rmtree(tmp_path / "ws")
    assert led.usage()["total"]["steps"] == 1


def test_permissions_and_read_only_reader(led, tmp_path):
    assert stat.S_IMODE(os.stat(led.path).st_mode) == 0o660
    rec(led, [step("a")])
    ro = Ledger(led.path, readonly=True)
    assert ro.usage()["total"]["steps"] == 1
    import sqlite3
    with pytest.raises(sqlite3.OperationalError):
        with ro._conn() as c:
            c.execute("DELETE FROM usage_step")
    # the web must work before the worker ever created the file
    assert Ledger(tmp_path / "missing.db", readonly=True).usage()["groups"] == []
    assert not (tmp_path / "missing.db").exists()


def test_transcript_steps_have_phase_model_and_compaction(tmp_path):
    from app.monitor import TranscriptTail
    from tests.test_monitor import _write
    p = tmp_path / "last-phase-1b-x-attempt-1-pid5.jsonl"
    _write(p, {"type": "message.updated", "properties": {"info": {"id": "m1", "agent": "compaction",
                                                                   "providerID": "pv", "modelID": "mo"}}},
           {"type": "message.part.updated", "properties": {"time": 2000, "part": {
               "id": "prt", "type": "step-finish", "messageID": "m1", "sessionID": "s",
               "tokens": {"input": 1}, "cost": 0}}})
    t = TranscriptTail(tmp_path)
    t.poll({5}, now=1.0)
    (s,) = t.drain_steps()
    assert (s["phase"], s["agent"], s["provider"], s["model"], s["ts"]) == ("1b", "compaction", "pv", "mo", 2.0)
    assert t.drain_steps() == []


def test_runner_ingests_while_running_and_survives_a_kill(tmp_path):
    led = Ledger(tmp_path / "data" / "ledger.db")
    db, runner, jid, job = setup(tmp_path, "steps_hang", ledger=led, ingest_every=0.1)
    th = threading.Thread(target=runner.run, args=(job,))
    th.start()
    for _ in range(100):  # wait until the steps are on record, while the job is still running
        if led.usage(include_synthetic=True)["total"]["steps"] == 2:
            break
        time.sleep(0.1)
    assert db.get(jid)["status"] == "running"
    db.request_cancel(jid)
    th.join(15)
    assert db.get(jid)["status"] == "cancelled"
    u = led.usage(group="phase")
    assert u["total"]["steps"] == 2 and u["groups"][0]["key"] == "1"  # transcript name says phase 1; dup prt_1 ignored
    assert u["total"]["tokens"]["input"] == 300


def test_ledger_failure_does_not_fail_the_job(tmp_path):
    class Broken(Ledger):
        def record(self, **kw):
            import sqlite3
            raise sqlite3.OperationalError("disk full")
    led = Broken(tmp_path / "data" / "ledger.db")
    db, runner, jid, job = setup(tmp_path, "steps", ledger=led, ingest_every=0.1)
    assert runner.run(job) == "done"


def test_usage_api(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import main
    from app.auth import Auth, hash_password
    from tests.test_api import PW
    led = Ledger(tmp_path / "l.db")
    rec(led, [step("a", provider="local-ollama")], project="p1")
    monkeypatch.setattr(main, "ledger", Ledger(led.path, readonly=True))
    monkeypatch.setattr(main, "auth", Auth({"ana": hash_password(PW)}, b"k" * 32))
    c = TestClient(main.app)
    assert c.get("/api/usage").status_code == 401
    assert c.post("/api/login", json={"username": "ana", "password": PW}).status_code == 200
    d = c.get("/api/usage?project=p1&group=model").json()
    assert d["total"]["steps"] == 1 and d["groups"][0]["key"] == "claude-x"
    assert c.get("/api/usage?group=nope").status_code == 400
    assert c.get("/api/usage?real=1").json()["total"]["steps"] == 0


def test_uncached_cost_prices_cache_tokens_as_plain_input(led, tmp_path):
    led.load_prices(prices(tmp_path, [{"valid_from": "2023-01-01", "input": 2, "output": 10, "cache_read": 0.1, "cache_write": 2.5}]))
    s = step("a", i=0, o=100_000)
    s["tokens"]["cache"] = {"read": 1_000_000, "write": 1_000_000}
    rec(led, [s], profile="api")
    t = led.usage()["total"]
    assert round(t["cost"], 6) == round(0.1 + 2.5 + 1.0, 6)
    assert round(t["cost_uncached"], 6) == round(2 + 2 + 1.0, 6)


def test_usage_of_the_current_audit_only(monkeypatch, tmp_path):
    """After archiving or emptying, the dashboard starts from zero; a rescue brings the spending back."""
    from fastapi.testclient import TestClient
    from app import main
    from app.auth import Auth, hash_password
    from app.itemdb import ItemDB
    from app.jobs import JobDB
    from tests.test_api import FIX, PW
    db = JobDB(tmp_path / "jobs.db")
    jid = db.enqueue("phase-1", None, "ana", ItemDB(FIX))
    db.finish(jid, "done", 0)
    led = Ledger(tmp_path / "l.db")
    rec(led, [step("a")], project="p1", job=jid)
    rec(led, [step("b")], project="p1", job=jid + 100)                # another audit's job
    monkeypatch.setattr(main, "ledger", Ledger(led.path, readonly=True))
    monkeypatch.setattr(main, "jobs", db)
    monkeypatch.setattr(main, "auth", Auth({"ana": hash_password(PW)}, b"k" * 32))
    c = TestClient(main.app)
    assert c.post("/api/login", json={"username": "ana", "password": PW}).status_code == 200
    assert c.get("/api/usage").json()["total"]["steps"] == 2
    assert c.get("/api/usage?scope=current").json()["total"]["steps"] == 1
    db.tag_audit("discarded-20261127-120000")
    assert c.get("/api/usage?scope=current").json()["total"]["steps"] == 0
    assert c.get("/api/usage").json()["total"]["steps"] == 2              # nothing is erased
    db.untag_audit("discarded-20261127-120000")
    assert c.get("/api/usage?scope=current").json()["total"]["steps"] == 1
    assert c.get("/api/usage?scope=bad").status_code == 422
