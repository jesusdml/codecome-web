# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Runs a job and publishes its real pulse in the database.

No shell: the command is an argument list from jobs.build_argv.
The process runs in its own group so it can be measured and killed as a whole.
"""

from __future__ import annotations

import collections
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

import sqlite3
import sys

import yaml

from . import oneshot
from .jobs import MAINT_ACTIONS, JobDB, build_argv, err
from .safe_extract import ExtractError
from .ledger import Ledger
from .profiles import ProfileError, Profiles
from .monitor import (CPU_ACTIVE_PCT, LogTail, ProcSampler, TranscriptTail,
                      beat_summary, classify, host_info)

ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "USER", "TZ")
ENV_PREFIXES = ("CODECOME_", "OPENCODE_", "ANTHROPIC_", "OLLAMA_", "DOCKER_", "XDG_")


def clean_env(environ=None) -> dict[str, str]:
    environ = os.environ if environ is None else environ
    env = {k: v for k, v in environ.items() if k in ENV_KEEP or k.startswith(ENV_PREFIXES)}
    env.setdefault("TERM", "dumb")
    return env


PRODUCED_DIRS = ("itemdb", "runs")
PRODUCED_MAX = 500


def _kind(rel: str) -> str:
    parts = rel.split("/")
    if parts[0] == "runs":
        return "summary"
    return {"findings": "finding", "notes": "note", "evidence": "evidence", "reports": "report"}.get(
        parts[1] if len(parts) > 2 else "", "other")


def fs_clock(root: Path) -> float | None:
    """Current time as the filesystem under `root` would stamp a file's mtime.

    Do not compare mtimes against time.time(): Linux stamps inodes from the coarse realtime
    clock, which only advances on timer ticks (10 ms at HZ=100, observed up to ~20 ms behind on
    WSL2). A file written right after a time.time() start can therefore look OLDER than the start
    and be missed. Network/foreign filesystems may use another clock altogether. Stamping an
    anonymous temp file (O_TMPFILE when supported, otherwise created and unlinked at once) reads
    the very clock that will stamp the job's files. None if it cannot be done (caller falls back).
    """
    try:
        with tempfile.TemporaryFile(dir=root) as f:
            return os.fstat(f.fileno()).st_mtime
    except OSError:
        return None


def produced_files(root: Path, since: float) -> list[dict]:
    """Files under itemdb/ and runs/ modified since the job started.

    Valid only because there is ONE worker and one job at a time: any change in the window
    belongs to this job. Symlinks are skipped (the agent writes here, we must not follow it out).
    """
    out: list[dict] = []
    for d in PRODUCED_DIRS:
        base = Path(root) / d
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = [x for x in dirnames if not os.path.islink(os.path.join(dirpath, x))]
            for n in filenames:
                p = Path(dirpath) / n
                try:
                    if p.is_symlink() or p.stat().st_mtime < since:
                        continue
                except OSError:
                    continue
                rel = p.relative_to(root).as_posix()
                out.append({"path": rel, "kind": _kind(rel)})
    out.sort(key=lambda x: x["path"])
    return out[:PRODUCED_MAX]


class MaintError(Exception):
    """A maintenance handler's clean failure: stable code + params for the UI."""

    def __init__(self, code: str, /, **params):
        super().__init__(code)
        self.code = code
        self.params = params


# handler(job, secret: bytearray | None, log: Callable[[str], None]) -> dict (stored as the job result)
MaintHandler = Callable[[dict, "bytearray | None", Callable[[str], None]], dict]


class Runner:
    def __init__(self, db: JobDB, root: Path, *, profiles: Profiles | None = None,
                 argv_builder: Callable = build_argv,
                 timeout: float = 6 * 3600, interval: float = 2.0, grace: float = 10.0,
                 stall_s: float = 180.0, clock: Callable[[], float] = time.time,
                 ledger: Ledger | None = None, ingest_every: float = 15.0,
                 maintenance: dict[str, MaintHandler] | None = None, secrets_dir: Path | None = None):
        self.db = db
        self.root = Path(root)
        self.profiles = profiles
        self.argv_builder = argv_builder
        self.timeout = timeout
        self.interval = interval
        self.grace = grace
        self.stall_s = stall_s
        self.clock = clock
        self.ledger = ledger
        self.ingest_every = ingest_every
        self.maintenance = maintenance or {}
        self.secrets_dir = Path(secrets_dir) if secrets_dir else None

    def _project(self) -> str:
        try:
            cfg = yaml.safe_load((self.root / "codecome.yml").read_text(encoding="utf-8")) or {}
            return str((cfg.get("project") or {}).get("name") or "unknown")
        except (OSError, ValueError, yaml.YAMLError):
            return "unknown"

    def _ingest(self, job: dict, tail: TranscriptTail) -> None:
        """Ledger errors must never fail or stall the job: usage is bookkeeping."""
        steps = tail.drain_steps()
        if not self.ledger or not steps:
            return
        phase = job["action"].removeprefix("phase-")
        try:
            self.ledger.record(project=self._project(), user=job.get("requested_by") or "?", job_id=job["id"],
                               finding=job.get("finding"), profile=job.get("profile"), steps=steps,
                               fallback_phase=phase)
        except (sqlite3.Error, OSError, KeyError) as e:
            print(f"ledger: {e!r}", file=sys.stderr)
            tail._steps = steps + tail._steps  # retry on the next round

    @staticmethod
    def _killpg(pgid: int, sig: int) -> None:
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def run_maintenance(self, job: dict) -> str:
        """Archive / rescue / upload: Python code in this process, no shell, no profile, no ledger.
        The secret (if any) is read once from its one-shot file and wiped from memory at the end."""
        jid = job["id"]
        name = self.db.take_secret_name(jid)
        secret = None
        log_path = Path(job["log_path"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        started = self.clock()
        try:
            with open(log_path, "a", encoding="utf-8") as logf:
                def log(msg: str) -> None:
                    now = self.clock()
                    logf.write(time.strftime("%H:%M:%S ") + msg + "\n")
                    logf.flush()
                    self.db.update_live(jid, {"t": now, "elapsed": round(now - started, 1), "state": "working",
                                              "params": {}, "last_line": msg})
                    self.db.beat(os.getpid())

                handler = self.maintenance.get(job["action"])
                if handler is None:
                    self.db.finish(jid, "failed", None, err("maintenance_unavailable", action=job["action"]))
                    return "failed"
                try:
                    if name:
                        if self.secrets_dir is None:
                            raise oneshot.SecretError("secret_missing")
                        secret = oneshot.take(self.secrets_dir, name)
                    result = handler(job, secret, log)
                except oneshot.SecretError as e:
                    log(f"failed: {e.code}")
                    self.db.finish(jid, "failed", None, err(e.code))
                    return "failed"
                except ExtractError as e:
                    log(f"failed: {e.code} {e.detail}")
                    self.db.finish(jid, "failed", None, err("extract_" + e.code))  # error.extract_<code>
                    return "failed"
                except MaintError as e:
                    log(f"failed: {e.code}")
                    self.db.finish(jid, "failed", None, err(e.code, **e.params))
                    return "failed"
                except Exception as e:  # never the message: it could quote a path or worse
                    log(f"failed: {type(e).__name__}")
                    self.db.finish(jid, "failed", None, err("maintenance_failed", detail=type(e).__name__))
                    return "failed"
                self.db.set_result(jid, result or {})
                log("done")
        finally:
            oneshot.wipe(secret)
            if name and self.secrets_dir is not None:
                oneshot.discard(self.secrets_dir, name)
        self.db.finish(jid, "done", 0)
        return "done"

    def run(self, job: dict) -> str:
        jid = job["id"]
        if job["action"] in MAINT_ACTIONS:
            return self.run_maintenance(job)
        log_path = Path(job["log_path"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = clean_env()
        if self.profiles is not None:
            try:  # the profile is re-read now: if it was removed or disabled, the job fails cleanly
                env.update(self.profiles.env(job.get("profile"), job["action"], job.get("model")))
            except ProfileError as e:
                self.db.finish(jid, "failed", None, err("profile_unavailable", reason=e.code, name=job.get("profile") or ""))
                return "failed"
        # Taken before launch: nothing touches itemdb/ or runs/ between claim and here, so the
        # window still covers exactly this job, but measured with the filesystem's own clock.
        fs_since = fs_clock(self.root)
        try:
            argv = self.argv_builder(job["action"], job["finding"])
            with open(log_path, "ab", buffering=0) as logf:
                proc = subprocess.Popen(
                    argv, cwd=self.root, stdin=subprocess.DEVNULL, stdout=logf,
                    stderr=subprocess.STDOUT, env=env, start_new_session=True,
                )
        except (OSError, ValueError) as e:
            self.db.finish(jid, "failed", None, err("launch_failed", detail=str(e)))
            return "failed"

        pgid = proc.pid
        sampler = ProcSampler(pgid)
        logtail = LogTail(log_path)
        tail = TranscriptTail(self.root / "tmp")
        cpu_hist: collections.deque[float] = collections.deque(maxlen=90)
        started = self.clock()
        last_signal = started
        cancelled = timed_out = False
        term_at: float | None = None
        last_ingest = started

        while True:
            rc = proc.poll()
            now = self.clock()

            s = sampler.sample()
            logtail.poll(now)
            tail.poll(set(s["pids"]), now)
            cpu_hist.append(s["cpu_pct"])
            if s["cpu_pct"] >= CPU_ACTIVE_PCT:
                last_signal = now
            for t in (logtail.last_output_at, tail.last_event_at):
                if t and t > last_signal:
                    last_signal = t

            snap = tail.snapshot(now)
            age = now - last_signal
            state, params = classify(age=age, agent_status=snap["status"],
                                     tps=snap["tps"], stall_s=self.stall_s)
            if state == "retrying" and snap["retry"]:
                params = {**params, **snap["retry"]}
            if rc is not None:
                state, params = "finished", {"code": rc}
            elif term_at is not None:
                state, params = "stopping", {}
            live = {
                "t": now, "pid": pgid, "elapsed": round(now - started, 1),
                "state": state, "params": params, "since_signal": round(age, 1),
                "since_output": None if logtail.last_output_at is None else round(now - logtail.last_output_at, 1),
                "last_line": logtail.last_line, "log_bytes": logtail.bytes,
                "procs": s["procs"], "rss": s["rss"], "cpu_pct": s["cpu_pct"],
                "io_bps": s["io_bps"], "cpu_hist": list(cpu_hist),
                "agent": snap, "host": host_info(),
                "beats": beat_summary(tail.beats or logtail.beats, now),
            }
            self.db.update_live(jid, live)
            self.db.beat(os.getpid())
            # While running, not only at the end: if the process dies, what was spent is on record.
            if rc is not None or now - last_ingest >= self.ingest_every:
                self._ingest(job, tail)
                last_ingest = now
            if rc is not None:
                break

            if not cancelled and self.db.cancel_requested(jid):
                cancelled, term_at = True, now
                self._killpg(pgid, signal.SIGTERM)
            elif not timed_out and not cancelled and now - started > self.timeout:
                timed_out, term_at = True, now
                self._killpg(pgid, signal.SIGTERM)
            if term_at is not None and now - term_at > self.grace:
                self._killpg(pgid, signal.SIGKILL)
            time.sleep(self.interval)

        self._killpg(pgid, signal.SIGKILL)  # leave no orphans from the group
        # Partial output of failed or cancelled jobs counts too: it explains what is half done.
        since = fs_since if fs_since is not None else (job.get("started_at") or started)
        self.db.set_produced(jid, produced_files(self.root, since))
        if cancelled:
            status, error = "cancelled", None
        elif timed_out:
            status, error = "failed", err("timeout")
        elif rc == 0:
            status, error = "done", None
        else:
            status, error = "failed", err("exit_code", code=rc)
        self.db.finish(jid, status, rc, error)
        return status


def work_loop(db: JobDB, runner: Runner, log_dir: Path, should_stop: Callable[[], bool],
              idle_sleep: float = 1.0) -> None:
    """One worker = one job at a time."""
    db.mark_interrupted()
    while not should_stop():
        db.beat(os.getpid())
        job = db.claim_next(log_dir)
        if job:
            runner.run(job)
        else:
            time.sleep(idle_sleep)
