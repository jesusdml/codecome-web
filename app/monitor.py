# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Real signals that a phase is alive. Nothing simulated.

Sources:
  - /proc of the process group the job starts (CPU, memory, I/O).
  - the JSONL transcripts CodeCome writes in <root>/tmp (agent state,
    model streaming, tokens, heartbeats).
  - the job's own output (last line and its age).
  - host state (load and temperatures).
"""

from __future__ import annotations

import collections
import json
import os
import re
import time
from pathlib import Path

CLK = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
PID_RE = re.compile(r"-pid(\d+)")
PHASE_RE = re.compile(r"^last-phase-(\d[abc]?)-")  # transcript name carries the sub-phase (1a/1b/1c)
CPU_ACTIVE_PCT = 1.0  # below this it does not count as activity
TPS_WINDOW_S = 10.0
MAX_READ = 1_000_000


# ---------------------------------------------------------------- /proc

def _read_proc(pid: int):
    """(pgrp, state, cpu_ticks, rss_bytes, io_bytes), or None if it no longer exists."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            raw = f.read().decode("utf-8", "replace")
        rest = raw[raw.rindex(")") + 2:].split()
        # campos: 3 estado, 5 pgrp, 14 utime, 15 stime, 16 cutime, 17 cstime
        state, pgrp = rest[0], int(rest[2])
        ticks = sum(int(rest[i]) for i in (11, 12, 13, 14))
        with open(f"/proc/{pid}/statm") as f:
            rss = int(f.read().split()[1]) * PAGE
        io = 0
        try:
            with open(f"/proc/{pid}/io") as f:
                for line in f:
                    k, _, v = line.partition(":")
                    if k in ("rchar", "wchar"):
                        io += int(v)
        except OSError:
            pass
        return pgrp, state, ticks, rss, io
    except (OSError, ValueError, IndexError):
        return None


class ProcSampler:
    """Measures the whole process group (make, python, opencode, children...)."""

    def __init__(self, pgid: int, clock=time.monotonic):
        self.pgid = pgid
        self._clock = clock
        self._prev: tuple[float, int, int] | None = None

    def sample(self) -> dict:
        procs = rss = ticks = io = 0
        pids: list[int] = []
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            r = _read_proc(int(name))
            if not r or r[0] != self.pgid or r[1] == "Z":
                continue
            procs += 1
            pids.append(int(name))
            ticks += r[2]
            rss += r[3]
            io += r[4]
        t = self._clock()
        cpu_pct = io_bps = 0.0
        if self._prev:
            dt = t - self._prev[0]
            if dt > 0:
                cpu_pct = max(0.0, (ticks - self._prev[1]) / CLK / dt * 100)
                io_bps = max(0, io - self._prev[2]) / dt
        self._prev = (t, ticks, io)
        return {"procs": procs, "rss": rss, "cpu_pct": round(cpu_pct, 1),
                "io_bps": int(io_bps), "pids": pids}


def host_info(hwmon_root: str = "/sys/class/hwmon", thermal_root: str = "/sys/class/thermal") -> dict:
    """Load and temperatures per sensor (amdgpu, k10temp, drivetemp...). All optional."""
    d: dict = {}
    try:
        d["load1"] = round(os.getloadavg()[0], 2)
        d["cpus"] = os.cpu_count()
    except OSError:
        pass
    temps: dict[str, float] = {}

    def _read(p: Path) -> float | None:
        try:
            v = int(p.read_text().strip()) / 1000
        except (OSError, ValueError):
            return None
        return v if 0 < v < 150 else None

    for h in sorted(Path(hwmon_root).glob("hwmon*")):
        try:
            name = (h / "name").read_text().strip() or h.name
        except OSError:
            name = h.name
        vals = [v for v in (_read(p) for p in h.glob("temp*_input")) if v is not None]
        if vals:
            temps[name] = round(max(vals), 1)
    for z in sorted(Path(thermal_root).glob("thermal_zone*")):
        v = _read(z / "temp")
        try:
            name = (z / "type").read_text().strip() or z.name
        except OSError:
            name = z.name
        if v is not None and name not in temps:
            temps[name] = round(v, 1)
    if temps:
        d["temps"] = temps
        d["temp_c"] = max(temps.values())
    return d


# ------------------------------------------------------------ salida / log

def is_heartbeat_line(line: str) -> bool:
    """CodeCome's console prints `server heartbeat` on every beat: proof that the
    server is alive, NOT progress of the job."""
    t = line.strip().lower()
    return len(t) < 48 and t.endswith("server heartbeat")


def beat_summary(times, now: float, keep: int = 40) -> dict:
    """Recent heartbeats (age in s, oldest to newest) and the typical interval."""
    ts = sorted(times)[-keep:]
    gaps = sorted(b - a for a, b in zip(ts, ts[1:]) if b > a)
    return {
        "ages": [round(now - t, 1) for t in ts],
        "interval": round(gaps[len(gaps) // 2], 1) if gaps else None,
        "last_age": round(now - ts[-1], 1) if ts else None,
        "count": len(ts),
    }


class LogTail:
    """Follows the job output, telling real output apart from server heartbeats."""

    MAX_READ = 262144

    def __init__(self, path: Path):
        self.path = Path(path)
        self._offset = 0
        self._rest = ""
        self.bytes = 0
        self.last_output_at: float | None = None  # REAL output only (no heartbeats)
        self.last_line = ""
        self.beats: collections.deque[float] = collections.deque(maxlen=60)

    def poll(self, now: float) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        self.bytes = size
        if size <= self._offset:
            return
        start = max(self._offset, size - self.MAX_READ)
        if start > self._offset:  # a lot of text at once: keep the end
            self._rest = ""
        with open(self.path, "rb") as f:
            f.seek(start)
            chunk = f.read(size - start).decode("utf-8", "replace")
        self._offset = size
        *lines, self._rest = (self._rest + ANSI_RE.sub("", chunk).replace("\r", "\n")).split("\n")
        for line in lines:
            if not line.strip():
                continue
            if is_heartbeat_line(line):
                self.beats.append(now)
            else:
                self.last_output_at = now
                self.last_line = line.strip()[:200]


# --------------------------------------------------------------- transcript

def _add_usage(a: dict, b: dict) -> dict:
    """Sum two token dicts (nested keys such as cache.read included)."""
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict):
            out[k] = _add_usage(out.get(k) or {}, v)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = out.get(k, 0) + v
    return out


class TranscriptTail:
    """Reads CodeCome's JSONL transcripts whose PID belongs to the group."""

    def __init__(self, tmp_dir: Path):
        self.tmp_dir = Path(tmp_dir)
        self._files: dict[Path, list] = {}  # path -> [offset, partial_line]
        self.agent_status: str | None = None
        self.retry: dict | None = None
        self.model = self.agent = self.title = None
        self.tokens: dict | None = None
        self.cost = 0.0
        self.last_part: str | None = None
        self.last_tool: str | None = None
        self.codecome_event: str | None = None
        self.events = 0
        self.last_event_at: float | None = None  # progreso real (sin latidos)
        self.last_heartbeat_at: float | None = None
        self.beats: collections.deque[float] = collections.deque(maxlen=60)
        self._deltas: collections.deque[float] = collections.deque()
        # Phase 1 opens one session per sub-phase and a session reports CUMULATIVE totals, so the
        # job total is the sum over (transcript file, session id) of the latest report of each.
        self._usage: dict[tuple, tuple[dict, float]] = {}
        # Finished model steps waiting to be written to the ledger (drained by the runner).
        self._steps: list[dict] = []
        self._msgs: dict[str, dict] = {}  # message id -> agent/provider/model
        self._model_pair: tuple[str | None, str | None] = (None, None)

    def _discover(self, pids: set[int]) -> None:
        try:
            names = os.listdir(self.tmp_dir)
        except OSError:
            return
        for n in names:
            if not n.startswith("last-phase-") or not n.endswith(".jsonl"):
                continue
            m = PID_RE.search(n)
            p = self.tmp_dir / n
            if m and int(m.group(1)) in pids and p not in self._files and not p.is_symlink():
                self._files[p] = [0, ""]

    def poll(self, pids: set[int], now: float) -> None:
        self._discover(pids)
        for path, st in self._files.items():
            try:
                with open(path, "rb") as f:
                    f.seek(st[0])
                    data = f.read(MAX_READ)
            except OSError:
                continue
            st[0] += len(data)
            text = st[1] + data.decode("utf-8", "replace")
            *lines, st[1] = text.split("\n")
            for line in lines:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue  # lines that are not JSON
                if isinstance(e, dict):
                    self._on_event(e, now, path)

    def _on_event(self, e: dict, now: float, src=None) -> None:
        t = str(e.get("type", ""))
        props = e.get("properties") or {}
        if t == "server.heartbeat":
            self.last_heartbeat_at = now  # the server lives, but that is not progress
            self.beats.append(now)
            return
        self.events += 1
        self.last_event_at = now
        if t == "session.status":
            st = props.get("status") or {}
            self.agent_status = st.get("type")
            self.retry = {k: st.get(k) for k in ("attempt", "message") if k in st} if self.agent_status == "retry" else None
        elif t == "message.part.delta":
            self._deltas.append(now)
        elif t == "session.updated":
            info = props.get("info") or {}
            key = (src, info.get("id"))
            prev_tokens, prev_cost = self._usage.get(key, ({}, 0.0))
            self._usage[key] = (info.get("tokens") or prev_tokens, info.get("cost", prev_cost) or 0.0)
            totals: dict = {}
            for tk, _ in self._usage.values():
                totals = _add_usage(totals, tk)
            self.tokens = totals or self.tokens
            self.cost = sum(c for _, c in self._usage.values())
            self.agent = info.get("agent") or self.agent
            self.title = info.get("title") or self.title
            m = info.get("model") or {}
            if m.get("id"):
                self.model = f"{m.get('providerID', '?')}/{m['id']}"
                self._model_pair = (m.get("providerID"), m["id"])
        elif t == "message.updated":
            info = props.get("info") or {}
            m = info.get("model") if isinstance(info.get("model"), dict) else {}
            if info.get("id") and len(self._msgs) < 5000:
                self._msgs[info["id"]] = {
                    "agent": info.get("agent"),
                    "provider": info.get("providerID") or m.get("providerID"),
                    "model": info.get("modelID") or m.get("modelID") or m.get("id")}
        elif t == "message.part.updated":
            part = props.get("part") or {}
            if part.get("type") == "step-finish" and part.get("id") and part.get("sessionID"):
                msg = self._msgs.get(part.get("messageID"), {})
                pm = PHASE_RE.match(src.name) if src is not None else None
                self._steps.append({
                    "part_id": part["id"], "session_id": part["sessionID"], "phase": pm.group(1) if pm else None,
                    "ts": (e.get("properties", {}).get("time") or 0) / 1000 or now,
                    "tokens": part.get("tokens"), "cost": part.get("cost"), "agent": msg.get("agent"),
                    "provider": msg.get("provider") or self._model_pair[0],
                    "model": msg.get("model") or self._model_pair[1]})
            self.last_part = part.get("type") or self.last_part
            if part.get("type") == "tool":
                self.last_tool = part.get("tool") or self.last_tool
        elif t.startswith("codecome."):
            self.codecome_event = t

    def drain_steps(self) -> list[dict]:
        out, self._steps = self._steps, []
        return out

    def tps(self, now: float) -> float:
        while self._deltas and now - self._deltas[0] > TPS_WINDOW_S:
            self._deltas.popleft()
        return len(self._deltas) / TPS_WINDOW_S

    def snapshot(self, now: float) -> dict:
        return {
            "status": self.agent_status, "retry": self.retry, "model": self.model,
            "name": self.agent, "title": self.title, "tokens": self.tokens,
            "cost": self.cost, "tps": round(self.tps(now), 1), "last_part": self.last_part,
            "last_tool": self.last_tool, "events": self.events,
            "event_age": None if self.last_event_at is None else round(now - self.last_event_at, 1),
            "server_beat_age": None if self.last_heartbeat_at is None else round(now - self.last_heartbeat_at, 1),
        }


# --------------------------------------------------------------- estado

def classify(*, age: float, agent_status: str | None, tps: float, stall_s: float = 180.0) -> tuple[str, dict]:
    """State from real signals: (code, parameters). The UI provides the text.

    `age` = seconds without any signal. stall_s=180 matches CodeCome's
    CODECOME_BUSY_STALL_TIMEOUT.
    """
    if agent_status == "retry":
        return "retrying", {}
    if tps >= 0.5:
        return "generating", {"rate": round(tps)}
    if age >= stall_s:
        return "stalled", {"age": int(age)}
    if age < 30:
        return "working", {}
    if agent_status == "busy":
        return "waiting_model", {"age": int(age)}
    return "quiet", {"age": int(age)}
