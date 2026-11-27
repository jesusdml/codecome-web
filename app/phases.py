# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Static catalog of the CodeCome phases (design doc, section 2.3 and 9 step 1).

Pure data, no I/O and no logic: to change what "done" means for a phase, edit this
table. UI texts are NOT here; they live in the locale files under `phase.<key>.*`.
Artifact patterns are relative to the CodeCome workspace root.
"""

from __future__ import annotations

from dataclasses import dataclass

# Placeholder replaced with a finding id (CC-NNNN) for finding-scoped nodes.
FINDING = "{id}"

NOTES_1C = (
    "attack-surface.md", "execution-model.md", "trust-boundaries.md", "data-flow.md",
    "validation-model.md", "interesting-files.md", "security-assumptions.md",
    "file-risk-index.yml", "threat-model.md",
)


@dataclass(frozen=True)
class Node:
    """A phase or sub-phase whose state is derived from disk and jobs."""

    key: str                      # "1a", "2", "4"...; also the argument of gate-check.py
    scope: str                    # "repo" | "finding"
    needs: tuple[str, ...] = ()   # node keys that must be done first (method order)
    # Every pattern must match at least one file for the node to count as done.
    artifacts: tuple[str, ...] = ()
    summary: str | None = None    # glob of the run summary written by the agent
    # Finding-scoped only: statuses (itemdb directories) that count as done.
    done_statuses: tuple[str, ...] = ()


@dataclass(frozen=True)
class Phase:
    key: str                      # "1".."6"; matches jobs.ACTIONS as f"phase-{key}"
    card: str                     # UI card; 4 and 5 share one ("4-5")
    scope: str                    # "repo" | "finding"
    steps: tuple[Node, ...]       # only phase 1 has sub-phases
    node: Node | None = None      # the phase itself when it has no steps

    @property
    def action(self) -> str:
        return f"phase-{self.key}"

    @property
    def nodes(self) -> tuple[Node, ...]:
        return self.steps if self.steps else (self.node,)


_NOTES = "itemdb/notes/"

PHASES: tuple[Phase, ...] = (
    Phase("1", "1", "repo", steps=(
        Node("1a", "repo",
             artifacts=(_NOTES + "target-profile.md", _NOTES + "build-model.md", _NOTES + "codeql-plan.yml"),
             summary="runs/phase-1a-summary*"),
        Node("1b", "repo", needs=("1a",),
             artifacts=(_NOTES + "sandbox-plan.md",),
             summary="runs/phase-1b-summary*"),
        Node("1c", "repo", needs=("1b",),
             artifacts=tuple(_NOTES + n for n in NOTES_1C),
             summary="runs/phase-1c-summary*"),
    )),
    Phase("2", "2", "repo", steps=(), node=Node(
        "2", "repo", needs=("1c",),
        artifacts=("itemdb/findings/*/CC-*.md",),
        summary="runs/phase-2-summary*")),
    # Phase 3 changes findings in place, so only its summary proves it ran.
    Phase("3", "3", "repo", steps=(), node=Node(
        "3", "repo", needs=("2",),
        summary="runs/phase-3-summary*")),
    # 4 and 5 are per finding (matrix cells); `artifacts` use FINDING.
    Phase("4", "4-5", "finding", steps=(), node=Node(
        "4", "finding", needs=("2",),
        done_statuses=("CONFIRMED", "EXPLOITED"),
        summary=f"runs/phase-4-{FINDING}-summary*")),
    Phase("5", "4-5", "finding", steps=(), node=Node(
        "5", "finding", needs=("4",),
        artifacts=(f"itemdb/evidence/{FINDING}/exploits/*",),
        done_statuses=("EXPLOITED",),
        summary=f"runs/phase-5-{FINDING}-summary*")),
    Phase("6", "6", "repo", steps=(), node=Node(
        "6", "repo", needs=("2",),
        artifacts=("itemdb/reports/report.md",))),
)

BY_KEY: dict[str, Phase] = {p.key: p for p in PHASES}
NODES: dict[str, Node] = {n.key: n for p in PHASES for n in p.nodes}
