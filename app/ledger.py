# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Usage ledger (design doc section 12): tokens are facts, cost is a calculation.

Own SQLite file, separate from jobs.db. Only the worker writes; the web only reads.
Counts only, never prompt or response text. Archiving a workspace never touches it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_step (
  id INTEGER PRIMARY KEY,
  project TEXT NOT NULL, user TEXT NOT NULL, job_id INTEGER NOT NULL,
  phase TEXT NOT NULL, finding TEXT, session_id TEXT NOT NULL,
  part_id TEXT NOT NULL,            -- opencode step-finish id: dedup key
  ts REAL NOT NULL, provider TEXT, model TEXT, profile TEXT,
  kind TEXT NOT NULL,               -- 'work' | 'compaction'
  input INTEGER, output INTEGER, reasoning INTEGER, cache_read INTEGER, cache_write INTEGER,
  reported_cost REAL,               -- what opencode reported (0 for local models); for reconciliation
  synthetic INTEGER NOT NULL,       -- 1 = mock profile: invented numbers
  UNIQUE (job_id, part_id)
);
CREATE TABLE IF NOT EXISTS price (
  model TEXT, valid_from TEXT, input REAL, output REAL, cache_read REAL, cache_write REAL,
  PRIMARY KEY (model, valid_from)   -- dollars per million tokens
);
"""

GROUPS = ("phase", "day", "model", "user", "project")
TOKEN_COLS = ("input", "output", "reasoning", "cache_read", "cache_write")


def is_local(provider: str | None) -> bool:
    p = (provider or "").lower()
    return p.startswith("local") or "ollama" in p


def is_synthetic(profile: str | None, provider: str | None) -> bool:
    return profile == "mock" or (provider or "") == "test"


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


class Ledger:
    def __init__(self, path: Path, *, readonly: bool = False):
        self.path = Path(path)
        self.readonly = readonly
        if readonly:
            return  # the web never creates or migrates the file
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Two users of one group (web reads, worker writes), same trick as jobs.db: pre-create 0660
        # so -wal and -shm inherit it.
        os.close(os.open(self.path, os.O_CREAT | os.O_RDWR, 0o660))
        try:
            os.chmod(self.path, 0o660)
        except PermissionError:
            pass
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        if self.readonly:
            c = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=10)
        else:
            c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            c.execute("PRAGMA journal_mode=WAL")
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    # ---------- worker side ----------
    def record(self, *, project: str, user: str, job_id: int, finding: str | None,
               profile: str | None, steps: list[dict], fallback_phase: str) -> int:
        """Insert steps; re-ingesting the same transcript duplicates nothing. Returns rows added."""
        added = 0
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            for s in steps:
                tk = s.get("tokens") or {}
                cache = tk.get("cache") or {}
                cur = c.execute(
                    "INSERT OR IGNORE INTO usage_step(project,user,job_id,phase,finding,session_id,part_id,ts,"
                    "provider,model,profile,kind,input,output,reasoning,cache_read,cache_write,reported_cost,synthetic)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (project, user, job_id, s.get("phase") or fallback_phase, finding, s["session_id"], s["part_id"],
                     s.get("ts") or time.time(), s.get("provider"), s.get("model"), profile,
                     "compaction" if s.get("agent") == "compaction" else "work",
                     int(tk.get("input") or 0), int(tk.get("output") or 0), int(tk.get("reasoning") or 0),
                     int(cache.get("read") or 0), int(cache.get("write") or 0), float(s.get("cost") or 0),
                     int(is_synthetic(profile, s.get("provider")))))
                added += cur.rowcount
            c.execute("COMMIT")
        return added

    def load_prices(self, path: Path) -> int:
        """config/prices.json: {"models": {"<model>": [{"valid_from","input","output","cache_read","cache_write"}]}}.
        Upserts, so a corrected price applies retroactively (cost is computed on read)."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        n = 0
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            for model, rows in (data.get("models") or {}).items():
                for r in rows:
                    c.execute("INSERT OR REPLACE INTO price VALUES(?,?,?,?,?,?)",
                              (model, str(r["valid_from"]), float(r.get("input", 0)), float(r.get("output", 0)),
                               float(r.get("cache_read", 0)), float(r.get("cache_write", 0))))
                    n += 1
            c.execute("COMMIT")
        return n

    # ---------- web side (read only) ----------
    def _prices(self, c) -> dict[str, list[sqlite3.Row]]:
        out: dict[str, list] = {}
        for r in c.execute("SELECT * FROM price ORDER BY model, valid_from"):
            out.setdefault(r["model"], []).append(r)
        return out

    @staticmethod
    def _price_at(prices: dict, model: str | None, day: str):
        best = None
        for r in prices.get(model or "", []):
            if r["valid_from"] <= day:
                best = r
        return best

    def usage(self, *, project: str | None = None, since: float | None = None, until: float | None = None,
              group: str = "phase", real: bool = False, price_as: str | None = None,
              include_synthetic: bool = False, job_ids: list[int] | None = None) -> dict:
        """Totals and groups. Mock steps are excluded unless asked; `real` also drops local models.
        `price_as` prices every step as if it ran on that model (the "and with another model?" question)."""
        if group not in GROUPS:
            raise ValueError("invalid_group")
        if not self.path.exists() or job_ids == []:
            return self._empty(group)
        where, args = [], []
        if job_ids is not None:  # only these jobs (the current audit); bound parameters, never SQL text
            where.append(f"job_id IN ({','.join('?' * len(job_ids))})"); args.extend(int(j) for j in job_ids)
        if project is not None:
            where.append("project=?"); args.append(project)
        if since is not None:
            where.append("ts>=?"); args.append(since)
        if until is not None:
            where.append("ts<?"); args.append(until)
        if not include_synthetic:
            where.append("synthetic=0")
        sql = "SELECT * FROM usage_step" + (" WHERE " + " AND ".join(where) if where else "")
        try:
            with self._conn() as c:
                rows = c.execute(sql, args).fetchall()
                prices = self._prices(c)
        except sqlite3.OperationalError:
            return self._empty(group)  # file exists but the worker has not created the tables yet
        groups: dict[str, dict] = {}
        total = self._bucket()
        for r in rows:
            local = is_local(r["provider"])
            if real and local and not price_as:
                continue
            key = {"phase": r["phase"], "day": _day(r["ts"]), "model": r["model"] or "?",
                   "user": r["user"], "project": r["project"]}[group]
            cost, uncached, priced = self._cost(r, prices, price_as, local)
            for b in (groups.setdefault(key, self._bucket()), total):
                b["steps"] += 1
                for col in TOKEN_COLS:
                    b["tokens"][col] += r[col] or 0
                b["reported_cost"] += r["reported_cost"] or 0
                if priced:
                    b["cost"] += cost
                    b["cost_uncached"] += uncached
                else:
                    b["unpriced_steps"] += 1
                if r["kind"] == "compaction":
                    b["compaction_steps"] += 1
                if local:
                    b["local_steps"] += 1
        return {"group": group, "price_as": price_as, "total": total,
                "groups": [{"key": k, **v} for k, v in sorted(groups.items())]}

    @staticmethod
    def _cost(r, prices, price_as, local) -> tuple[float, float, bool]:
        """(cost, cost if nothing had been cached, priced?)."""
        if local and not price_as:
            return 0.0, 0.0, True  # real tokens, zero money
        p = Ledger._price_at(prices, price_as or r["model"], _day(r["ts"]))
        if not p:
            return 0.0, 0.0, False
        # Reasoning tokens are billed as output.
        out = ((r["output"] or 0) + (r["reasoning"] or 0)) * p["output"]
        inp, cr, cw = r["input"] or 0, r["cache_read"] or 0, r["cache_write"] or 0
        usd = (inp * p["input"] + out + cr * p["cache_read"] + cw * p["cache_write"]) / 1e6
        return usd, ((inp + cr + cw) * p["input"] + out) / 1e6, True

    @staticmethod
    def _bucket() -> dict:
        return {"steps": 0, "tokens": {c: 0 for c in TOKEN_COLS}, "cost": 0.0, "cost_uncached": 0.0, "reported_cost": 0.0,
                "unpriced_steps": 0, "compaction_steps": 0, "local_steps": 0}

    def _empty(self, group: str) -> dict:
        return {"group": group, "price_as": None, "total": self._bucket(), "groups": []}
