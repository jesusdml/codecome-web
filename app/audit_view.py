# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Assembly of GET /api/audit and GET /api/phases/{key} (design doc section 7).

Only codes and numbers leave here: every user-facing sentence is translated by the front end.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path

import yaml

from . import audit, phases
from .gates import GateRunner, launch_reasons
from .itemdb import ItemDB
from .jobs import ACTIVE
from .runner import _kind as artifact_kind

GATE_MAX_AGE = 15.0  # the page polls every 2 s; do not spawn gate processes at that rate
PHASE_KEYS = {"1", "2", "3", "4", "5", "6"}
STEP_KEYS = {"1a": "1", "1b": "1", "1c": "1"}


def _run(j: dict, now: float) -> dict:
    agent = ((j.get("live") or {}).get("agent")) or {}
    start, end = j.get("started_at"), j.get("finished_at")
    live = j.get("live") or {}
    out = {"job": j["id"], "status": j["status"], "finding": j.get("finding"), "profile": j.get("profile"),
           "picked": j.get("model"), "model": agent.get("model"), "started_at": start, "finished_at": end,
           "elapsed": round((end or now) - start, 1) if start else None,
           "tokens": agent.get("tokens"), "cost": agent.get("cost")}
    if j["status"] == "running":  # enough to follow the job from the home page without opening it
        out["live"] = {"state": live.get("state"), "params": live.get("params") or {},
                       "last_tool": agent.get("last_tool"), "tps": agent.get("tps"),
                       "since_output": live.get("since_output"), "last_line": (live.get("last_line") or "")[:200]}
    return out


def _reference(js: list[dict], last: dict | None) -> dict | None:
    """'Last time it took...': a real measurement of earlier runs with the same profile, not an ETA."""
    if not last:
        return None
    el = [j["finished_at"] - j["started_at"] for j in js
          if j["status"] == "done" and j["id"] != last["job"] and j.get("started_at") and j.get("finished_at")
          and j.get("profile") == last["profile"]]
    return {"profile": last["profile"], "median_elapsed": round(statistics.median(el), 1)} if el else None


def project_name(root: Path) -> str | None:
    """codecome.yml `project.name` (written by whoever set up the workspace): shown as text only."""
    try:
        p = Path(root) / "codecome.yml"
        if p.is_symlink() or p.stat().st_size > 64 * 1024:
            return None
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        name = (cfg.get("project") or {}).get("name") if isinstance(cfg, dict) else None
        return str(name)[:80] if name else None
    except (OSError, ValueError, yaml.YAMLError, AttributeError):
        return None


def build_audit(*, root: Path, jobs: list[dict], itemdb: ItemDB, gates: GateRunner, worker: dict,
                profiles: dict, now: float | None = None, marks: dict | None = None) -> dict:
    now = now or time.time()
    state = audit.derive(root, jobs, marks)
    system: list[dict] = []
    if not worker.get("alive"):
        system.append({"code": "worker_down", "params": {}})
    default = next((p for p in profiles["profiles"] if p["name"] == profiles["default"]), None)
    if default is not None and not default["enabled"]:
        system.append({"code": "profile_disabled", "params": {"name": default["name"]}})

    def launch(action: str, finding: str | None = None) -> dict:
        reasons = launch_reasons(action, finding, root=root, jobs=jobs, itemdb=itemdb, gates=gates,
                                 gate_max_age=GATE_MAX_AGE) + system
        return {"allowed": not reasons, "reasons": reasons}

    out_phases = []
    for p in state["phases"]:
        action = f"phase-{p['key']}"
        js = [j for j in jobs if j["action"] == action]  # newest first
        e = dict(p)
        e["scope"] = {"kind": phases.BY_KEY[p["key"]].scope}
        e["open_questions"] = 0  # summaries with open questions arrive with the documents step
        if e["scope"]["kind"] == "repo":
            e["launch"] = launch(action)
            last = _run(js[0], now) if js else None
            e["last_run"], e["reference"] = last, _reference(js, last)
            for s in e.get("steps", []):
                s["launch"] = e["launch"]  # one job runs every sub-phase
                s["last_run"] = last
        else:
            last = _run(js[0], now) if js else None
            e["last_run"], e["reference"] = last, _reference(js, last)
        out_phases.append(e)

    matrix = []
    for row in state["matrix"]:
        r = dict(row)
        for k in ("4", "5"):
            cell = dict(r["p" + k])
            cell["launch"] = (launch(f"phase-{k}", row["id"]) if cell["status"] not in ("assumed", "na")
                              else {"allowed": False, "reasons": []})
            r["p" + k] = cell
        matrix.append(r)

    running = next((j for j in reversed(jobs) if j["status"] == "running"), None)
    active = None
    if running:
        key = running["action"].removeprefix("phase-")
        sub = None
        if key == "1":
            sub = next((s["key"] for s in out_phases[0]["steps"] if s["status"] in ACTIVE), None)
        active = {"job": running["id"], "phase": key, "sub": sub, "finding": running.get("finding"),
                  "run": _run(running, now)}
    queue = [{"job": j["id"], "phase": j["action"].removeprefix("phase-"), "finding": j.get("finding")}
             for j in sorted(jobs, key=lambda j: j["id"]) if j["status"] == "queued"]

    return {
        "worker": worker, "active": active, "queue": queue, "project": project_name(root),
        "progress": {"phases_done": state["phases_done"], "phases_total": state["phases_total"]},
        "next": _next(out_phases, state, active),
        "open_questions": 0,
        "phases": out_phases, "matrix": matrix,
        "profiles": {"default": profiles["default"], "list": profiles["profiles"], "models": profiles.get("models", [])},
    }


SYSTEM_CODES = ("worker_down", "profile_disabled")


def _blockers(p: dict) -> list[dict]:
    """Reasons about the method itself; a stopped worker must not hide the real next step."""
    return [r for r in p["launch"]["reasons"] if r["code"] not in SYSTEM_CODES]


def _next(ph: list[dict], state: dict, active: dict | None) -> dict:
    """One code plus params; the banner text is the front end's job."""
    if active:
        return {"code": "running", "params": {"job": active["job"]}}
    by = {p["key"]: p for p in ph}
    for p in ph:  # a queued job already covers this phase: do not suggest launching it again
        if p["status"] == "queued" and p.get("launch"):
            jid = next((r["params"]["id"] for r in p["launch"]["reasons"] if r["code"] == "job_active"), None)
            if jid:
                return {"code": "running", "params": {"job": jid}}
    for k in ("1", "2", "3"):
        if by[k]["status"] != "done":
            if not _blockers(by[k]):
                return {"code": "launch_phase", "params": {"phase": k}}
            return {"code": "blocked", "params": {"phase": k, "reason": _blockers(by[k])[0]}}
    pending = sum(1 for r in state["matrix"] if r["status"] == "PENDING" and r["p4"]["status"] not in ("assumed", "done"))
    if pending:
        return {"code": "validate_pending", "params": {"n": pending}}
    if by["6"]["status"] != "done":
        if not _blockers(by["6"]):
            return {"code": "launch_phase", "params": {"phase": "6"}}
        return {"code": "blocked", "params": {"phase": "6", "reason": _blockers(by["6"])[0]}}
    return {"code": "all_done", "params": {}}


def build_phase(key: str, *, root: Path, jobs: list[dict]) -> dict | None:
    """Catalog + full run history + artifacts found on disk. `key` must be from the closed list."""
    step = None
    if key in STEP_KEYS:
        step, key = key, STEP_KEYS[key]
    if key not in PHASE_KEYS:
        return None
    ph = phases.BY_KEY[key]
    nodes = [n for n in ph.nodes if step in (None, n.key)]
    runs = [{"job": j["id"], "status": j["status"], "finding": j.get("finding"), "profile": j.get("profile"),
             "requested_by": j["requested_by"], "created_at": j["created_at"], "started_at": j.get("started_at"),
             "finished_at": j.get("finished_at"), "error": j.get("error"),
             "produced": None if j.get("produced") is None else len(j["produced"])}
            for j in jobs if j["action"] == ph.action]
    files = {}
    root = Path(root).resolve()
    for n in nodes:
        for pat in n.artifacts + ((n.summary,) if n.summary else ()):
            for f in root.glob(pat.replace(phases.FINDING, "CC-*")):
                if f.is_file() and not f.is_symlink():
                    rel = f.relative_to(root).as_posix()
                    st = f.stat()
                    files[rel] = {"path": rel, "kind": artifact_kind(rel), "size": st.st_size, "mtime": st.st_mtime}
    return {"key": step or key, "phase": key, "action": ph.action, "card": ph.card, "scope": {"kind": ph.scope},
            "steps": [{"key": n.key, "needs": list(n.needs), "artifacts": list(n.artifacts),
                       "summary": n.summary} for n in nodes],
            "runs": runs, "artifacts": sorted(files.values(), key=lambda a: a["path"])}
