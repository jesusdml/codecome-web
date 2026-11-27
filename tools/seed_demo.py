#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Demo workspace for the web UI (design doc section 11, item 5): an audit halfway through, with findings
in the five states and a job history, WITHOUT running CodeCome or any model. Everything is invented demo data.

Usage:
    .venv/bin/python tools/seed_demo.py DEMO_DIR --tools-from /path/to/CodeCome/tools
    .venv/bin/python tools/seed_demo.py DEMO_DIR --beat      # keep the demo "worker" and running job alive

Then run the web with CODECOME_ROOT=DEMO_DIR/workspace CODECOME_DATA=DEMO_DIR/data.
`--beat` only writes heartbeats into the demo jobs.db; it never runs anything.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from app.jobs import JobDB  # noqa: E402
from app.ledger import Ledger  # noqa: E402
from app.phases import NOTES_1C  # noqa: E402
from tools.build_mock_scenario import VULNS, finding_md  # noqa: E402

MODEL = "claude-sonnet-5-5"
EXPLOIT_MODEL = "claude-opus-4-8"  # phase 5 runs on the more permissive model (profile phase_env)


def w(root: Path, rel: str, text: str = "Demo content.\n") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def extra(fid, title, sev, status, validation="NOT_STARTED"):
    return (f'---\nid: "{fid}"\ntitle: "{title}"\nstatus: "{status}"\nseverity: "{sev}"\nconfidence: "MEDIUM"\n'
            f'category: "Demo"\nvalidation:\n  status: "{validation}"\n---\n\n# Summary\n\n{title} (demo data).\n')


def promoted(md: str, status: str) -> str:
    """What tools/move-finding.py does in a real run: folder status plus validation/exploitation status."""
    md = md.replace('status: "PENDING"', f'status: "{status}"', 1)
    md = md.replace('validation:\n  status: "NOT_STARTED"', 'validation:\n  status: "CONFIRMED"')
    if status == "EXPLOITED":
        md = md.replace('exploitation:\n  status: "NOT_STARTED"', 'exploitation:\n  status: "EXPLOITED"')
    return md


def seed_workspace(ws: Path, tools_from: Path | None) -> None:
    if ws.exists():
        shutil.rmtree(ws)
    shutil.copytree(BASE / "tests" / "fixtures" / "demo-src", ws / "src")
    if tools_from:  # the real gate-check.py, so the gates in the demo are real
        shutil.copytree(tools_from, ws / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    w(ws, "codecome.yml", "project:\n  name: demo-shop\n")
    for n in ("target-profile.md", "build-model.md", "codeql-plan.yml", "sandbox-plan.md", *NOTES_1C):
        w(ws, f"itemdb/notes/{n}", f"# {n}\n\nDemo note.\n")
    for ph in ("1a", "1b", "1c", "2", "3"):
        w(ws, f"runs/phase-{ph}-summary-2026-11-24-000000.md", f"# Phase {ph} summary\n\nDemo run.\n")
        w(ws, f"runs/phase-{ph}-summary.md", f"# Phase {ph} summary\n\nDemo run.\n")
    sqli, cmd, trav = (VULNS[0], VULNS[1], VULNS[2])
    # CC-0002 command injection: exploited. CC-0001 SQL injection: confirmed. CC-0003 traversal: pending (running).
    w(ws, f"itemdb/findings/EXPLOITED/{cmd['id']}-{cmd['slug']}.md", promoted(finding_md(cmd, validated=True), "EXPLOITED"))
    w(ws, f"itemdb/evidence/{cmd['id']}/README.md", "# Evidence (demo)\n")
    w(ws, f"itemdb/evidence/{cmd['id']}/exploits/poc.sh", "curl 'http://target/ping?host=127.0.0.1;id'\n")
    w(ws, f"itemdb/findings/CONFIRMED/{sqli['id']}-{sqli['slug']}.md", promoted(finding_md(sqli, validated=True), "CONFIRMED"))
    w(ws, f"itemdb/evidence/{sqli['id']}/README.md", "# Evidence (demo)\n")
    w(ws, f"itemdb/findings/PENDING/{trav['id']}-{trav['slug']}.md", finding_md(trav, reviewed=True))
    w(ws, "itemdb/findings/PENDING/CC-0004-no-authentication.md", extra("CC-0004", "No authentication on any route", "LOW", "PENDING"))
    w(ws, "itemdb/findings/REJECTED/CC-0005-debug-banner.md", extra("CC-0005", "Server banner discloses the Python version", "INFO", "REJECTED", "REJECTED"))
    w(ws, "itemdb/evidence/CC-0005/README.md", "# Could not reproduce (demo)\n")
    w(ws, "itemdb/findings/DUPLICATE/CC-0006-shell-in-ping.md", extra("CC-0006", "Shell metacharacters reach /ping", "HIGH", "DUPLICATE"))


def live(now, started, tokens_in, tokens_out, state="generating"):
    return {"t": now, "pid": 4242, "elapsed": round(now - started, 1), "state": state,
            "params": {"rate": 14} if state == "generating" else {}, "since_signal": 1.2, "since_output": 3.0,
            "last_line": "reading src/app.py", "log_bytes": 2048, "procs": 6, "rss": 1_900_000_000, "cpu_pct": 87.0,
            "io_bps": 52000, "cpu_hist": [60, 75, 90, 85, 88, 92, 80, 87],
            "agent": {"status": "busy", "model": MODEL, "name": "validator", "tps": 14.0,
                      "tokens": {"input": tokens_in, "output": tokens_out, "reasoning": 0}, "cost": 0,
                      "last_tool": "bash", "events": 812},
            "host": {"load1": 3.1, "cpus": 8, "temp_c": 71.0}, "beats": {}}


def seed_jobs(data: Path) -> None:
    db_path = data / "jobs.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    db = JobDB(db_path)
    now = time.time()
    h = 3600
    # (action, finding, status, started ago, duration, tokens in/out, error)
    plan = [
        ("phase-1", None, "failed", 30 * h, 1800, (210_000, 9_000), {"code": "timeout", "params": {}}),
        ("phase-1", None, "done", 28 * h, 2 * h + 14 * 60, (640_000, 41_000), None),
        ("phase-2", None, "done", 25 * h, 52 * 60, (380_000, 27_500), None),
        ("phase-3", None, "done", 23 * h, 41 * 60, (120_000, 8_100), None),
        ("phase-4", "CC-0002", "done", 20 * h, 33 * 60, (150_000, 11_000), None),
        ("phase-4", "CC-0001", "done", 18 * h, 29 * 60, (140_000, 9_700), None),
        ("phase-4", "CC-0005", "done", 16 * h, 22 * 60, (90_000, 5_200), None),
        ("phase-5", "CC-0002", "done", 12 * h, 48 * 60, (230_000, 19_000), None),
    ]
    with db._conn() as c:
        for action, finding, status, ago, dur, (ti, to), error in plan:
            start = now - ago
            c.execute("INSERT INTO jobs(action,finding,status,requested_by,created_at,started_at,finished_at,exit_code,"
                      "error,profile,live,produced) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (action, finding, status, "ana", start - 5, start, start + dur, 0 if status == "done" else None,
                       json.dumps(error) if error else None, "local",
                       json.dumps(live(start + dur, start, ti, to, "finished")), "[]"))
        start = now - 17 * 60  # the job running right now
        c.execute("INSERT INTO jobs(action,finding,status,requested_by,created_at,started_at,profile,live,log_path) "
                  "VALUES('phase-4','CC-0003','running','ana',?,?,'api',?,?)",
                  (start - 5, start, json.dumps(live(now, start, 98_000, 6_400)), str(data / "logs" / "demo.log")))
        c.execute("INSERT INTO jobs(action,finding,status,requested_by,created_at,profile) "
                  "VALUES('phase-4','CC-0004','queued','ana',?,'api')", (now - 60,))
    (data / "logs").mkdir(parents=True, exist_ok=True)
    (data / "logs" / "demo.log").write_text(
        "== Phase 4: validate CC-0003 (demo log, nothing is running) ==\n"
        "starting sandbox... ok\nreading itemdb/findings/PENDING/CC-0003-path-traversal-file-name.md\n"
        "GET /file?name=../app.py -> 200 (demo)\nwriting itemdb/evidence/CC-0003/request.txt\n", encoding="utf-8")
    db.beat(os.getpid())
    seed_ledger(data, db)


def ledger_steps(job: dict, tokens_in: int, tokens_out: int) -> list[dict]:
    """Invented per-step usage shaped like a real opencode run: the first step writes the big prompt
    to the cache, later steps read it back and add a little fresh input."""
    sub = ["1a", "1b", "1c"] if job["action"] == "phase-1" else [job["action"].removeprefix("phase-")]
    model = EXPLOIT_MODEL if job["action"] == "phase-5" else MODEL
    n = 12 * len(sub)
    out, start, dur = [], job["started_at"], (job["finished_at"] or time.time()) - job["started_at"]
    for i in range(n):
        first = i % 12 == 0
        out.append({"part_id": f"demo-{job['id']}-{i}", "session_id": f"ses-{job['id']}-{i // 12}",
                    "ts": start + dur * (i + 1) / (n + 1), "provider": "anthropic", "model": model,
                    "phase": sub[i // 12], "agent": "demo", "cost": 0,
                    "tokens": {"input": tokens_in // (n * 20), "output": tokens_out // n, "reasoning": 0,
                               "cache": {"write": 21_000 if first else tokens_in // (n * 8),
                                         "read": 0 if first else tokens_in // n}}})
    return out


def seed_ledger(data: Path, db: JobDB) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(str(data / "ledger.db") + suffix).unlink(missing_ok=True)
    led = Ledger(data / "ledger.db")
    led.load_prices(BASE / "config" / "prices.json")
    for j in db.list_jobs(None):
        tk = ((j.get("live") or {}).get("agent") or {}).get("tokens")
        if tk and j["started_at"]:
            led.record(project="demo-shop", user=j["requested_by"], job_id=j["id"], finding=j.get("finding"),
                       profile="api", steps=ledger_steps(j, tk["input"], tk["output"]), fallback_phase="1")


def beat(data: Path) -> None:
    """Keeps the demo alive: a fresh worker heartbeat and a running job whose clock and tokens advance."""
    db = JobDB(data / "jobs.db")
    print("demo heartbeat running; Ctrl-C to stop")
    while True:
        running = [j for j in db.list_jobs(None, status="running")]
        for j in running:
            now = time.time()
            tk = (j["live"] or {}).get("agent", {}).get("tokens", {"input": 98_000, "output": 6_400})
            db.update_live(j["id"], live(now, j["started_at"], tk["input"] + 350, tk["output"] + 40))
        db.beat(os.getpid())
        time.sleep(2)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("demo_dir", type=Path)
    ap.add_argument("--tools-from", type=Path, help="CodeCome tools/ to copy (real gates)")
    ap.add_argument("--beat", action="store_true", help="only keep the demo worker heartbeat alive")
    a = ap.parse_args()
    data = a.demo_dir / "data"
    if a.beat:
        beat(data)
    else:
        data.mkdir(parents=True, exist_ok=True)
        seed_workspace(a.demo_dir / "workspace", a.tools_from)
        seed_jobs(data)
        print(f"demo ready in {a.demo_dir}")
