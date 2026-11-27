# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Derived audit state (design doc, section 2.3). Pure: reads the workspace, writes nothing.

`derive(root, jobs)` answers "what happened" (`status`) for every phase, sub-phase and
phase 4/5 x finding cell. "What can be launched" (gates, reasons) is a separate concern.
"""

from __future__ import annotations

from pathlib import Path

from . import phases
from .itemdb import STATUSES, ItemDB, validate_id

ACTIVE = ("queued", "running")
FAILED = ("failed", "cancelled", "interrupted")  # all shown as `failed`, the reason is kept
DISCARDED = ("REJECTED", "DUPLICATE")


def _matches(root: Path, pattern: str) -> int:
    # Symlinks are ignored: the agent writes into this tree and must not make us read elsewhere.
    return sum(1 for p in root.glob(pattern) if p.is_file() and not p.is_symlink())


def _artifacts(root: Path, node: phases.Node, fid: str | None = None) -> dict:
    pats = [a.replace(phases.FINDING, fid) if fid else a for a in node.artifacts]
    found = sum(1 for p in pats if _matches(root, p))
    summary = None
    if node.summary:
        summary = bool(_matches(root, node.summary.replace(phases.FINDING, fid) if fid else node.summary))
    return {"found": found, "expected": len(pats), "summary": summary}


def _repo_done(a: dict) -> bool:
    # Phase 3 and 6 have a single proof (summary / report), so empty `expected` is fine.
    return a["found"] == a["expected"] and a["summary"] is not False and (a["expected"] > 0 or a["summary"])


def _node_jobs(jobs: list[dict], action: str, fid: str | None = None) -> list[dict]:
    return sorted((j for j in jobs if j["action"] == action and (fid is None or j.get("finding") == fid)),
                  key=lambda j: j["id"])


def _job_status(js: list[dict]) -> tuple[str, dict | None]:
    """running/queued > failed (last job only) > none. `done` comes from disk, not from jobs."""
    for st in ("running", "queued"):
        for j in js:
            if j["status"] == st:
                return st, j
    if js and js[-1]["status"] in FAILED:
        return "failed", js[-1]
    return "none", None


def _result(status: str, job: dict | None, artifacts: dict, js: list[dict], needs_unmet=()) -> dict:
    out = {
        "status": status,
        "job": job["id"] if job else (js[-1]["id"] if js else None),
        "failure": job["status"] if status == "failed" else None,  # failed/cancelled/interrupted
        "artifacts": artifacts,
        # Done on disk but no web job finished it: someone ran `make` by hand.
        "external": status == "done" and not any(j["status"] == "done" for j in js),
        "needs_unmet": list(needs_unmet),
    }
    return out


def _rejected_by_validation(root: Path, f) -> bool:
    v = f.meta.get("validation")
    started = isinstance(v, dict) and v.get("status") not in (None, "", "NOT_STARTED")
    return f.status == "REJECTED" and started and _matches(root, f"itemdb/evidence/{f.id}/*") > 0


def _cell4(root: Path, f, js: list[dict]) -> tuple[str, dict]:
    a = _artifacts(root, phases.NODES["4"], f.id)
    done = f.status in phases.NODES["4"].done_statuses or _rejected_by_validation(root, f)
    return ("done" if done else "none"), a


def _cell5(root: Path, f, js: list[dict]) -> tuple[str, dict]:
    node = phases.NODES["5"]
    a = _artifacts(root, node, f.id)
    return ("done" if f.status in node.done_statuses and _repo_done({**a, "summary": None}) else "none"), a


def _aggregate(cells: list[str], partial: bool = False) -> str:
    for st in ("running", "queued"):
        if st in cells:
            return st
    settled = ("done", "assumed", "na")  # assumed/na only exist for phases 4/5
    if cells and all(c in settled for c in cells):
        return "done"
    if partial and any(c in settled for c in cells):
        return "partial"  # phases 4/5: some findings done, others still open (or skipped, later)
    return "failed" if "failed" in cells else "none"


def derive(root: Path, jobs: list[dict], marks: dict | None = None) -> dict:
    root = Path(root).resolve()
    found = ItemDB(root).list_findings()
    result: dict = {"phases": [], "matrix": [], "findings_by_status": {s: 0 for s in STATUSES}}
    for f in found:
        result["findings_by_status"][f.status] += 1
    status_of: dict[str, str] = {}

    def unmet(node):
        return [d for d in node.needs if status_of.get(d) != "done"]

    for ph in phases.PHASES:
        if ph.scope == "finding":
            continue
        js = _node_jobs(jobs, ph.action)
        jstatus, jjob = _job_status(js)
        steps = []
        pending_job_used = False
        for node in ph.nodes:
            a = _artifacts(root, node)
            if _repo_done(a):
                st, job = "done", None
            elif jstatus != "none" and not pending_job_used:
                # One job runs all sub-phases; the first one not on disk is the one in progress.
                st, job, pending_job_used = jstatus, jjob, True
            else:
                st, job = "none", None
            status_of[node.key] = st
            steps.append({"key": node.key, **_result(st, job, a, js, unmet(node))})
        # A live job always wins over `done`: it may be a re-run (precedence rule 1).
        if jstatus in ACTIVE and not any(s["status"] in ACTIVE for s in steps):
            steps[-1]["status"] = jstatus
            steps[-1]["job"] = jjob["id"]
            steps[-1]["external"] = False
            status_of[steps[-1]["key"]] = jstatus
        agg = _aggregate([s["status"] for s in steps])
        status_of[ph.key] = agg
        entry = {"key": ph.key, "status": agg, "scope": "repo", "jobs": len(js)}
        if ph.steps:
            entry["steps"] = steps
            entry["needs_unmet"] = steps[0]["needs_unmet"]
        else:
            entry.update({k: v for k, v in steps[0].items() if k != "key"})
        result["phases"].append(entry)

    # Phases 4/5: one cell per finding. Placed in catalog order (4 and 5 come after 3).
    rows = []
    for f in sorted(found, key=lambda f: (_sev_rank(f.meta.get("severity")), f.id)):
        validate_id(f.id)
        row = {"id": f.id, "title": f.meta.get("title", ""), "severity": f.meta.get("severity", ""),
               "status": f.status, "discarded": f.status in DISCARDED}
        for key, cell in (("4", _cell4), ("5", _cell5)):
            ph = phases.BY_KEY[key]
            js = _node_jobs(jobs, ph.action, f.id)
            jstatus, jjob = _job_status(js)
            disk, a = cell(root, f, js)
            st, job = ("done", None) if disk == "done" and jstatus not in ACTIVE else (jstatus, jjob)
            # The user's "assume" (accept without proof) settles a cell that is not done or running.
            if st not in ("done",) + tuple(ACTIVE):
                m = (marks or {}).get((f.id, key))
                if m or (key == "5" and (marks or {}).get((f.id, "4")) and f.status == "PENDING"):
                    st = "assumed" if m else "na"
            row["p" + key] = _result(st, job, a, js)
            if (marks or {}).get((f.id, key)):
                row["p" + key]["mark"] = marks[(f.id, key)]
        rows.append(row)
    result["matrix"] = rows
    live = [r for r in rows if not r["discarded"]]
    for key in ("4", "5"):
        cells = [r["p" + key]["status"] for r in live]
        counts: dict[str, int] = {}
        for c in cells:
            counts[c] = counts.get(c, 0) + 1
        status_of[key] = _aggregate(cells, partial=True)
        result["phases"].insert(_phase_index(result["phases"], key),
                                {"key": key, "status": status_of[key], "scope": "finding", "status_counts": counts})
    result["phases_done"] = sum(1 for p in result["phases"] if p["status"] == "done")
    result["phases_total"] = len(phases.PHASES)
    return result


_SEV = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


def _sev_rank(sev) -> int:
    return _SEV.get(str(sev or "").upper(), 5)


def _phase_index(entries: list[dict], key: str) -> int:
    """Insert 4 and 5 after phase 3 and before phase 6, keeping 1..6 order."""
    return sum(1 for e in entries if int(e["key"]) < int(key))
