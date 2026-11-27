# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
from app import jobs, phases


def test_keys_unique_and_cover_all_jobs_actions():
    node_keys = [n.key for p in phases.PHASES for n in p.nodes]
    assert len(node_keys) == len(set(node_keys))
    assert [p.key for p in phases.PHASES] == ["1", "2", "3", "4", "5", "6"]
    # Every job action maps to one phase and vice versa.
    assert {p.action for p in phases.PHASES} == set(jobs.ACTIONS)


def test_only_phase_1_has_substeps():
    assert [n.key for n in phases.BY_KEY["1"].steps] == ["1a", "1b", "1c"]
    for p in phases.PHASES:
        if p.key != "1":
            assert p.steps == () and p.node is not None


def test_needs_point_to_existing_nodes_and_are_acyclic():
    for key, node in phases.NODES.items():
        for dep in node.needs:
            assert dep in phases.NODES, f"{key} needs unknown {dep}"
    def depth(k, seen=()):
        assert k not in seen, f"cycle at {k}"
        return 1 + max((depth(d, seen + (k,)) for d in phases.NODES[k].needs), default=0)
    for k in phases.NODES:
        depth(k)


def test_scope_matches_finding_placeholder_and_needs_finding():
    for p in phases.PHASES:
        assert (p.scope == "finding") == (p.action in jobs.NEEDS_FINDING)
        for n in p.nodes:
            assert n.scope == p.scope
            patterns = n.artifacts + ((n.summary,) if n.summary else ())
            has_placeholder = any(phases.FINDING in x for x in patterns)
            if n.scope == "repo":
                assert not has_placeholder and not n.done_statuses
            else:
                assert n.done_statuses  # finding nodes are decided by finding status


def test_every_node_has_a_done_criterion():
    for n in phases.NODES.values():
        assert n.artifacts or n.summary or n.done_statuses, n.key


def test_phase_1c_has_nine_notes_and_4_5_share_a_card():
    assert len(phases.NOTES_1C) == 9 and len(set(phases.NOTES_1C)) == 9
    assert phases.BY_KEY["4"].card == phases.BY_KEY["5"].card == "4-5"
    # Patterns are workspace-relative, never absolute or escaping.
    for n in phases.NODES.values():
        for x in n.artifacts + ((n.summary,) if n.summary else ()):
            assert not x.startswith("/") and ".." not in x
