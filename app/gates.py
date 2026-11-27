# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Launch gates (design doc D5, section 11): the server is the guardian, not only the UI.

`tools/gate-check.py N [CC-ID]` has no machine-readable contract: the verdict is its exit code
(0 = ready). Structured reasons come from the disk state computed by `audit.derive`; the gate's
English text is kept only as untrusted "technical detail" (plain text, never HTML).
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import audit, phases
from .itemdb import ItemDB
from .jobs import ACTIVE, MAINT_ACTIONS, NEEDS_FINDING
from .monitor import ANSI_RE

ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "TZ")
CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
DETAIL_MAX = 4000
# Reasons that block a launch on their own; the others (finding_*, job_active) are reported
# by `JobDB.enqueue` with their existing status codes.
BLOCKING = ("needs_phase", "gate_failed", "gate_unavailable", "finding_busy", "maintenance_running", "src_empty")


@dataclass(frozen=True)
class GateResult:
    ok: bool
    detail: str = ""
    error: str | None = None  # None | "timeout" | "missing" | "failed_to_run"


def clean(text: str) -> str:
    """The gate output reflects files the agent wrote: strip escapes and control characters."""
    return CTRL_RE.sub("", ANSI_RE.sub("", text)).strip()[:DETAIL_MAX]


def _run(argv: list[str], cwd: Path, timeout: float) -> tuple[int, str]:
    """No shell, list of arguments, minimal environment, own process group so a timeout kills it all."""
    env = {k: v for k, v in os.environ.items() if k in ENV_KEEP}
    env.update({"NO_COLOR": "1", "PYTHONDONTWRITEBYTECODE": "1", "TERM": "dumb"})
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.communicate()
        raise
    return proc.returncode, out.decode("utf-8", "replace")


class GateRunner:
    def __init__(self, root: Path, *, python: str | None = None, timeout: float = 20.0, ttl: float = 2.0,
                 execute: Callable[[list[str], Path, float], tuple[int, str]] = _run,
                 clock: Callable[[], float] = time.monotonic):
        self.root = Path(root).resolve()  # the script path and cwd must not depend on the caller's cwd
        self.python = python or sys.executable
        self.timeout = timeout
        self.ttl = ttl
        self._exec = execute
        self._clock = clock
        self._cache: dict[tuple, tuple[float, GateResult]] = {}

    def check(self, key: str, finding: str | None = None, max_age: float | None = None) -> GateResult:
        # `key` and `finding` come from catalogs / validated ids, never from raw user text.
        # `max_age` lets the polled audit view accept older results than the launch endpoint does.
        ck = (key, finding)
        hit = self._cache.get(ck)
        if hit and self._clock() - hit[0] < (self.ttl if max_age is None else max_age):
            return hit[1]
        script = self.root / "tools" / "gate-check.py"
        if not script.is_file():
            res = GateResult(False, error="missing")
        else:
            argv = [self.python, str(script), key] + ([finding] if finding else [])
            try:
                code, out = self._exec(argv, self.root, self.timeout)
                res = GateResult(code == 0, clean(out))
            except subprocess.TimeoutExpired:
                res = GateResult(False, error="timeout")
            except OSError:
                res = GateResult(False, error="failed_to_run")
        self._cache[ck] = (self._clock(), res)
        return res


PLACEHOLDERS = (".gitkeep", ".keep")  # an "empty" src/ kept in git still counts as empty


def src_has_code(root: Path) -> bool:
    """src/ exists (not a symlink) and has at least one real entry: archived or never uploaded = no."""
    src = Path(root) / "src"
    try:
        if src.is_symlink() or not src.is_dir():
            return False
        with os.scandir(src) as it:
            return any(e.name not in PLACEHOLDERS for e in it)
    except PermissionError:
        return True  # cannot look inside: do not block launches on a guess (the worker re-checks)
    except OSError:
        return False


def launch_reasons(action: str, finding: str | None, *, root: Path, jobs: list[dict], itemdb: ItemDB,
                   gates: GateRunner, gate_max_age: float | None = None) -> list[dict]:
    """Why `action` cannot be launched now ([] = allowed). Structural reasons come first; the gate only
    runs when nothing else already explains the block."""
    key = action.removeprefix("phase-")
    phase = phases.BY_KEY[key]
    reasons: list[dict] = []

    # Archive / rescue / upload move the whole workspace: nothing may start under them.
    maint = next((j for j in jobs if j["action"] in MAINT_ACTIONS and j["status"] in ACTIVE), None)
    if maint:
        return [{"code": "maintenance_running", "params": {"id": maint["id"], "action": maint["action"]}}]
    if not src_has_code(root):
        return [{"code": "src_empty", "params": {}}]

    for j in jobs:
        if j["action"] == action and (j.get("finding") or None) == finding and j["status"] in ACTIVE:
            reasons.append({"code": "job_active", "params": {"id": j["id"]}})
            break
    # CodeCome moves the finding (e.g. to CONFIRMED) while the phase is still running: never stack
    # another phase on a finding that has any active job, or it would act on a half-finished state.
    if finding:
        busy = next((j for j in jobs if (j.get("finding") or None) == finding and j["action"] != action
                     and j["status"] in ACTIVE), None)
        if busy:
            reasons.append({"code": "finding_busy", "params": {"id": busy["id"], "action": busy["action"]}})

    if phase.scope == "finding":
        f = next((x for x in itemdb.list_findings() if x.id == finding), None)
        if f is None:
            reasons.append({"code": "finding_not_found", "params": {}})
        elif f.status != NEEDS_FINDING[action]:
            reasons.append({"code": "finding_wrong_state",
                            "params": {"action": action, "needs": NEEDS_FINDING[action]}})
    else:
        state = audit.derive(root, jobs)
        status = {}
        for p in state["phases"]:
            status[p["key"]] = p["status"]
            for s in p.get("steps", []):
                status[s["key"]] = s["status"]
        for dep in phase.nodes[0].needs:  # order of the method
            if status.get(dep) != "done":
                reasons.append({"code": "needs_phase", "params": {"phase": dep}})
                break

    if not reasons:
        g = gates.check(key, finding) if gate_max_age is None else gates.check(key, finding, gate_max_age)
        if g.error:
            reasons.append({"code": "gate_unavailable", "params": {"phase": key, "reason": g.error}})
        elif not g.ok:
            reasons.append({"code": "gate_failed", "params": {"phase": key, "detail": g.detail}})
    return reasons
