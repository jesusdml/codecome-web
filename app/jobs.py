# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Job queue in SQLite (WAL). The web only enqueues and reads; the worker runs.

The web never builds free-form commands: it only accepts actions from ACTIONS, and
`build_argv` is the only door to a process.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .itemdb import FindingNotFound, ItemDB, validate_id

ACTIONS = tuple(f"phase-{i}" for i in range(1, 7))
# Maintenance jobs (v1.1): file work on the workspace done by the worker in Python, never `make`.
# Exclusive: one may only start with nothing else active, and no phase starts while one is active.
MAINT_ACTIONS = ("archive", "rescue", "upload", "reset")
TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")  # archive id / upload id
# Per-finding phases: the status the finding must be in to launch them.
NEEDS_FINDING = {"phase-4": "PENDING", "phase-5": "CONFIRMED"}
ACTIVE = ("queued", "running")
FINAL = ("done", "failed", "cancelled", "interrupted")
WORKER_STALE_S = 10.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action TEXT NOT NULL,
  finding TEXT,
  status TEXT NOT NULL,
  requested_by TEXT NOT NULL,
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL,
  exit_code INTEGER,
  error TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  log_path TEXT,
  live TEXT,
  profile TEXT,
  produced TEXT,
  model TEXT,
  target TEXT,   -- maintenance: archive id (rescue) / upload id (upload)
  secret TEXT,   -- maintenance: name of the one-shot secret file (never the secret itself)
  audit TEXT,    -- NULL = the current audit; else the id of the archive it was moved into
  result TEXT,   -- maintenance: JSON summary written by the worker
  params TEXT    -- maintenance: JSON options from the web (never secrets), e.g. the new project name
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, id);
CREATE TABLE IF NOT EXISTS worker (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  pid INTEGER,
  beat REAL
);
-- "Assumed": the user decided not to validate (phase 4) or not to exploit (phase 5) a finding.
-- Web-side decision only; CodeCome's itemdb/ is never touched.
CREATE TABLE IF NOT EXISTS finding_mark (
  finding TEXT NOT NULL,
  phase TEXT NOT NULL CHECK (phase IN ('4', '5')),
  mark TEXT NOT NULL CHECK (mark = 'assumed'),
  by TEXT NOT NULL,
  at REAL NOT NULL,
  reason TEXT NOT NULL DEFAULT '',  -- why it was not proven: plain text written by the user
  PRIMARY KEY (finding, phase)
);
"""


class CodedError(ValueError):
    """Error with a stable code and parameters: the UI translates it."""

    def __init__(self, code: str, /, **params):
        super().__init__(code)
        self.code = code
        self.params = params


class InvalidJob(CodedError):
    pass


class DuplicateJob(CodedError):
    pass


def err(code: str, /, **params) -> str:
    """Text stored in the `error` column: JSON {code, params}."""
    return json.dumps({"code": code, "params": params})


def build_argv(action: str, finding: str | None) -> list[str]:
    """Argument list (no shell). Validates the action and the id."""
    if action not in ACTIONS:
        raise InvalidJob("action_not_allowed")
    argv = ["make", action]
    if action in NEEDS_FINDING:
        if not finding:
            raise InvalidJob("finding_required")
        argv.append(f"FINDING={validate_id(finding)}")
    elif finding:
        raise InvalidJob("finding_not_allowed")
    return argv


class JobDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Two users of the same group (web and worker) share the database. SQLite creates
        # its files 0644 even with umask 0002, so it is pre-created 0660: the -wal and
        # -shm files inherit the mode of the main file.
        os.close(os.open(self.path, os.O_CREAT | os.O_RDWR, 0o660))
        try:
            os.chmod(self.path, 0o660)
        except PermissionError:
            pass  # the other user already created (and set) it
        with self._conn() as c:
            c.executescript(SCHEMA)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(jobs)")}
            mcols = {r["name"] for r in c.execute("PRAGMA table_info(finding_mark)")}
            if "reason" not in mcols:
                try:
                    c.execute("ALTER TABLE finding_mark ADD COLUMN reason TEXT NOT NULL DEFAULT ''")
                except sqlite3.OperationalError:
                    pass  # the other process (web/worker) already added it
            for col in ("profile", "produced", "model", "target", "secret", "audit", "result", "params"):  # databases created before these columns existed
                if col not in cols:
                    try:
                        c.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")
                    except sqlite3.OperationalError:
                        pass  # the other process (web/worker) already added it

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        try:
            yield c
        finally:
            c.close()

    # ---------- lado web ----------
    def enqueue(self, action: str, finding: str | None, user: str, itemdb: ItemDB,
                profile: str | None = None, model: str | None = None) -> int:
        build_argv(action, finding)  # valida
        if action in NEEDS_FINDING:
            try:
                f = itemdb.get_finding(finding)
            except FindingNotFound:
                raise InvalidJob("finding_not_found")
            if f.status != NEEDS_FINDING[action]:
                raise InvalidJob("finding_wrong_state", action=action, needs=NEEDS_FINDING[action])
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            maint = c.execute(f"SELECT id, action FROM jobs WHERE status IN ('queued','running') AND action IN ({_IN_MAINT})",
                              MAINT_ACTIONS).fetchone()
            if maint:
                c.execute("ROLLBACK")
                raise DuplicateJob("maintenance_running", id=maint["id"], action=maint["action"])
            dup = c.execute(
                "SELECT id FROM jobs WHERE action=? AND IFNULL(finding,'')=? AND status IN ('queued','running')",
                (action, finding or ""),
            ).fetchone()
            if dup:
                c.execute("ROLLBACK")
                raise DuplicateJob("duplicate_job", id=dup["id"])
            cur = c.execute(
                "INSERT INTO jobs(action,finding,status,requested_by,created_at,profile,model) VALUES(?,?,?,?,?,?,?)",
                (action, finding, "queued", user, time.time(), profile, model),
            )
            c.execute("COMMIT")
            return cur.lastrowid

    def enqueue_maintenance(self, action: str, user: str, *, target: str | None = None,
                            secret: str | None = None, params: dict | None = None) -> int:
        """Exclusive job: refused while ANY job is queued or running (and phases are refused while
        it is active, see `enqueue`). Checked and inserted in one IMMEDIATE transaction."""
        if action not in MAINT_ACTIONS:
            raise InvalidJob("action_not_allowed")
        if target is not None and not TARGET_RE.match(target):
            raise InvalidJob("invalid_target")
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            busy = c.execute("SELECT id, action FROM jobs WHERE status IN ('queued','running') ORDER BY id LIMIT 1").fetchone()
            if busy:
                c.execute("ROLLBACK")
                raise DuplicateJob("jobs_active", id=busy["id"], action=busy["action"])
            cur = c.execute(
                "INSERT INTO jobs(action,status,requested_by,created_at,target,secret,params) VALUES(?,?,?,?,?,?,?)",
                (action, "queued", user, time.time(), target, secret, json.dumps(params) if params else None),
            )
            c.execute("COMMIT")
            return cur.lastrowid

    def take_secret_name(self, job_id: int) -> str | None:
        """Worker: the secret file name of this job, cleared from the row as it is read."""
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT secret FROM jobs WHERE id=?", (job_id,)).fetchone()
            c.execute("UPDATE jobs SET secret=NULL WHERE id=?", (job_id,))
            c.execute("COMMIT")
        return row["secret"] if row else None

    def secret_name(self, job_id: int) -> str | None:
        """Web: to delete the file of a job cancelled before it started."""
        with self._conn() as c:
            row = c.execute("SELECT secret FROM jobs WHERE id=?", (job_id,)).fetchone()
        return row["secret"] if row else None

    def set_result(self, job_id: int, result: dict) -> None:
        with self._conn() as c:
            c.execute("UPDATE jobs SET result=? WHERE id=?", (json.dumps(result), job_id))

    # ---------- archive / rescue bookkeeping (worker) ----------
    def tag_audit(self, archive_id: str) -> int:
        """The finished phase jobs of the current audit now belong to `archive_id` (hidden)."""
        with self._conn() as c:
            return c.execute(
                f"UPDATE jobs SET audit=? WHERE audit IS NULL AND action IN ({_IN_PHASES}) AND status NOT IN ('queued','running')",
                (archive_id, *ACTIONS)).rowcount

    def untag_audit(self, archive_id: str) -> int:
        with self._conn() as c:
            return c.execute("UPDATE jobs SET audit=NULL WHERE audit=?", (archive_id,)).rowcount

    def export_marks(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM finding_mark ORDER BY finding, phase")]

    def replace_marks(self, marks: list[dict]) -> None:
        """Clear every mark and load `marks` (archive: []; rescue: the archived ones)."""
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("DELETE FROM finding_mark")
            for m in marks:
                c.execute("INSERT INTO finding_mark(finding,phase,mark,by,at,reason) VALUES(?,?,?,?,?,?)",
                          (m["finding"], m["phase"], m["mark"], m["by"], m["at"], m.get("reason") or ""))
            c.execute("COMMIT")

    def marks(self) -> dict[tuple[str, str], dict]:
        with self._conn() as c:
            return {(r["finding"], r["phase"]): {"mark": r["mark"], "by": r["by"], "at": r["at"], "reason": r["reason"]}
                    for r in c.execute("SELECT * FROM finding_mark")}

    def set_mark(self, finding: str, phase: str, user: str, reason: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO finding_mark(finding,phase,mark,by,at,reason) VALUES(?,?,'assumed',?,?,?)",
                      (finding, phase, user, time.time(), reason))

    def clear_mark(self, finding: str, phase: str) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM finding_mark WHERE finding=? AND phase=?", (finding, phase)).rowcount > 0

    def request_cancel(self, job_id: int) -> bool:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT status, action FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["status"] not in ACTIVE:
                c.execute("ROLLBACK")
                return False
            if row["status"] == "running" and row["action"] in MAINT_ACTIONS:
                c.execute("ROLLBACK")  # half-done file work must finish (or fail) on its own
                return False
            if row["status"] == "queued":  # never started: cancelled right away
                c.execute("UPDATE jobs SET status='cancelled', finished_at=? WHERE id=?",
                          (time.time(), job_id))
            else:
                c.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
            c.execute("COMMIT")
            return True

    def get(self, job_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _row(row) if row else None

    def list_jobs(self, limit: int | None = 50, *, action: str | None = None,
                  finding: str | None = None, status: str | None = None, audit: str | None = None) -> list[dict]:
        """Newest first. `limit=None` means no cap. Filters are bound parameters, never SQL text.
        Only the CURRENT audit by default; `audit=<archive id>` lists the jobs archived with it."""
        where, args = ["audit IS NULL"] if audit is None else ["audit=?"], [] if audit is None else [audit]
        for col, val in (("action", action), ("finding", finding), ("status", status)):
            if val is not None:
                where.append(f"{col}=?")
                args.append(val)
        sql = "SELECT * FROM jobs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [_row(r) for r in rows]

    def worker_state(self, now: float | None = None) -> dict:
        now = now or time.time()
        with self._conn() as c:
            row = c.execute("SELECT pid, beat FROM worker WHERE id=1").fetchone()
        if not row:
            return {"alive": False, "since": None}
        age = now - row["beat"]
        return {"alive": age < WORKER_STALE_S, "since": round(age, 1), "pid": row["pid"]}

    # ---------- lado worker ----------
    def beat(self, pid: int) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO worker(id,pid,beat) VALUES(1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET pid=excluded.pid, beat=excluded.beat",
                (pid, time.time()),
            )

    def mark_interrupted(self) -> int:
        """At worker start: whatever was left 'running' is no longer alive."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE jobs SET status='interrupted', finished_at=?, "
                "error=? WHERE status='running'",
                (time.time(), err("worker_restarted")),
            )
            return cur.rowcount

    def claim_next(self, log_dir: Path) -> dict | None:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if not row:
                c.execute("ROLLBACK")
                return None
            log = str(Path(log_dir) / f"{row['id']}.log")
            c.execute("UPDATE jobs SET status='running', started_at=?, log_path=? WHERE id=?",
                      (time.time(), log, row["id"]))
            c.execute("COMMIT")
        return self.get(row["id"])

    def update_live(self, job_id: int, live: dict) -> None:
        with self._conn() as c:
            c.execute("UPDATE jobs SET live=? WHERE id=?", (json.dumps(live), job_id))

    def set_produced(self, job_id: int, produced: list[dict]) -> None:
        with self._conn() as c:
            c.execute("UPDATE jobs SET produced=? WHERE id=?", (json.dumps(produced), job_id))

    def cancel_requested(self, job_id: int) -> bool:
        with self._conn() as c:
            row = c.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
        return bool(row and row["cancel_requested"])

    def finish(self, job_id: int, status: str, exit_code: int | None, error: str | None = None) -> None:
        assert status in FINAL
        with self._conn() as c:
            c.execute(
                "UPDATE jobs SET status=?, exit_code=?, error=?, finished_at=? WHERE id=?",
                (status, exit_code, error, time.time(), job_id),
            )


_IN_MAINT = ",".join("?" * len(MAINT_ACTIONS))
_IN_PHASES = ",".join("?" * len(ACTIONS))


def _row(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["live"] = json.loads(d["live"]) if d.get("live") else None
    d["result"] = json.loads(d["result"]) if d.get("result") else None
    d["params"] = json.loads(d["params"]) if d.get("params") else None
    d.pop("secret", None)  # only the worker needs the file name; never sent to the browser
    d["cancel_requested"] = bool(d["cancel_requested"])
    # None = job older than the `produced` column (the UI shows a dash); [] = produced nothing.
    d["produced"] = json.loads(d["produced"]) if d.get("produced") else (None if d.get("produced") is None else [])
    e = d.get("error")
    if e:
        try:
            d["error"] = json.loads(e)
        except ValueError:
            d["error"] = {"code": "unknown", "params": {"text": e}}
    return d
