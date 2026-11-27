# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""The mock script must keep satisfying what CodeCome's gates require (copied from its code)."""
import re

import pytest

from tools import build_mock_scenario as b
from tools import mock_llm

NOTES_1C = ["attack-surface.md", "execution-model.md", "trust-boundaries.md", "data-flow.md", "validation-model.md",
            "interesting-files.md", "file-risk-index.yml", "security-assumptions.md", "threat-model.md"]
THREAT = ["Threat Model Summary", "Scope", "System model", "Assets and security objectives", "Attacker model",
          "Trust boundary summary", "Existing controls", "Abuse-path themes for Phase 2",
          "Risk calibration for review focus", "Open questions for the user", "Re-run prompt hints"]
SUMMARY = ["Open questions for the user", "Re-run prompt hints"]


def writes(script):
    return {a["arguments"]["filePath"]: a["arguments"]["content"] for a in script
            if a.get("type") == "tool_call" and a["name"] == "write"}


def h1(text):
    return {m.group(1).strip().lower() for m in re.finditer(r"^# (\S.*)$", text, re.M)}


def test_every_scenario_ends_with_done_and_loads():
    d = b.build()
    for s in d["scenarios"]:
        assert s["script"][-1] == {"type": "done"}
    scen, _ = mock_llm.load_scenarios(__import__("pathlib").Path(__file__).resolve().parent.parent / "config" / "mock" / "phase1.json")
    assert [m for m, _ in scen] == list(b.SCENARIOS)


def test_titles_are_distinct_so_a_phase_never_matches_another():
    keys = list(b.SCENARIOS)
    for k in keys:
        assert [x for x in keys if x in k] == [k]


def test_1a_writes_what_gate_1a_needs():
    w = writes(b.phase_1a)
    assert {"itemdb/notes/target-profile.md", "itemdb/notes/build-model.md", "itemdb/notes/codeql-plan.yml"} <= set(w)
    s = next(v for k, v in w.items() if k.startswith("runs/phase-1a-summary"))
    assert set(map(str.lower, SUMMARY)) <= h1(s)


def test_1b_writes_the_plan_and_a_summary_with_headings():
    w = writes(b.phase_1b)
    assert "itemdb/notes/sandbox-plan.md" in w
    # CodeCome's checks look for the name WITHOUT a date (even though the prompt asks for the dated one)
    assert "runs/phase-1b-summary.md" in w and "runs/phase-1b-summary-2026-11-24-000001.md" in w
    assert set(map(str.lower, SUMMARY)) <= h1(w["runs/phase-1b-summary.md"])


def test_1c_writes_all_notes_and_a_complete_threat_model():
    w = writes(b.phase_1c)
    assert all(f"itemdb/notes/{n}" in w for n in NOTES_1C)
    assert set(map(str.lower, THREAT)) <= h1(w["itemdb/notes/threat-model.md"])
    assert "runs/phase-1c-summary.md" in w
    assert set(map(str.lower, SUMMARY)) <= h1(w["runs/phase-1c-summary.md"])


def test_risk_index_is_valid_and_has_no_template_placeholder():
    import yaml
    data = yaml.safe_load(writes(b.phase_1c)["itemdb/notes/file-risk-index.yml"])
    assert isinstance(data["files"], list) and data["files"]
    for f in data["files"]:
        assert 1 <= f["score"] <= 5 and not f["path"].startswith("/") and "../" not in f["path"]
        assert f["path"] != "src/example/path/to/file.ext"


def test_codeql_plan_is_valid_yaml_with_schema_v2():
    import yaml
    plan = yaml.safe_load(writes(b.phase_1a)["itemdb/notes/codeql-plan.yml"])
    assert plan["schema_version"] == 2 and plan["analysis_units"][0]["languages"][0]["id"] == "python"


# ---- phases 2 and 3: mirror of CodeCome's Phase 2 quality rules (tools/findings/quality.py) ----
SECTIONS_2 = ["Summary", "Target context", "Affected code", "Vulnerability hypothesis", "Source-to-sink reasoning",
              "Attackability / trigger conditions", "Impact", "Validation plan", "Counter-analysis", "Validation result", "Evidence"]
FIELDS = ["id", "title", "status", "severity", "cvss_v4", "confidence", "category", "cwe", "language", "target_area", "files",
          "symbols", "entry_points", "sources", "sinks", "trust_boundary", "assets_at_risk", "validation", "exploitation",
          "created_at", "updated_at"]
TEMPLATE_MARKERS = ["Briefly describe the suspected vulnerability.", "Describe the relevant target type", "List the relevant files",
                    "Explain the suspected vulnerability.", "Describe the path from attacker-controlled", "Explain how an attacker",
                    "Explain the realistic security impact.", "Describe exactly how to prove or disprove the finding.",
                    "Try to disprove the finding."]


@pytest.mark.parametrize("reviewed", [False, True])
def test_findings_satisfy_phase2_quality_rules(reviewed):
    import yaml
    for v in b.VULNS:
        text = b.finding_md(v, reviewed=reviewed)
        front = yaml.safe_load(text.split("---")[1])
        assert all(f in front for f in FIELDS) and front["status"] == "PENDING"
        assert all(front[k] for k in ("files", "sources", "sinks", "assets_at_risk")) and front["symbols"]
        assert all(s.lower() in h1(text) for s in SECTIONS_2)
        assert not any(m.lower() in text.lower() for m in TEMPLATE_MARKERS)


def test_finding_filenames_follow_codecome_convention():
    for path in b.FINDING_PATHS:
        assert re.fullmatch(r"itemdb/findings/PENDING/CC-\d{4}-[a-z0-9]+[-_a-z0-9]*\.md", path)


def test_phase2_and_3_write_fresh_summaries_with_the_dated_name_codecome_globs():
    # Unlike 1b/1c, phases 2 and 3 look for runs/phase-N-summary-*.md (a glob), so the dated name is the right one.
    assert any(k.startswith("runs/phase-2-summary-") for k in writes(b.phase_2))
    assert any(k.startswith("runs/phase-3-summary-") for k in writes(b.phase_3))


def test_phase3_rewrites_the_same_findings_without_changing_status():
    assert set(writes(b.phase_3)) >= set(b.FINDING_PATHS)
    assert all('status: "PENDING"' in writes(b.phase_3)[p] for p in b.FINDING_PATHS)


def test_mock_profile_allows_phase2_without_a_sandbox():
    import json
    from pathlib import Path
    cfg = Path(__file__).resolve().parent.parent / "config"
    prof = json.loads((cfg / "profiles.json" if (cfg / "profiles.json").exists() else cfg / "profiles.example.json").read_text())
    assert prof["profiles"]["mock"]["env"]["CODECOME_ALLOW_NO_SANDBOX"] == "1"


# ---- phase 4: what CodeCome requires of a CONFIRMED finding (tools/findings/checks.py) ----
BANDS = {"MEDIUM": (4.0, 6.9), "HIGH": (7.0, 8.9), "CRITICAL": (9.0, 10.0)}


def test_every_cvss_score_sits_inside_the_band_of_its_severity():
    for v in b.VULNS:
        vec, score, why = b.CVSS[v["id"]]
        lo, hi = BANDS[v["severity"]]
        assert vec.startswith("CVSS:4.0/") and why.strip() and lo <= score <= hi, v["id"]


def test_validated_finding_has_what_confirmed_needs():
    import yaml
    for v in b.VULNS:
        text = b.finding_md(v, validated=True)
        front = yaml.safe_load(text.split("---")[1])
        assert front["status"] == "PENDING"  # the move tool flips it, as in a real run
        assert front["cvss_v4"]["vector"].startswith("CVSS:4.0/") and front["cvss_v4"]["justification"]
        assert front["validation"]["methods"] and front["validation"]["summary"]
        assert "# Remediation idea" in text and "```diff" in text  # CONFIRMED needs a code excerpt or diff
        assert "Not validated yet." not in text


def test_phase4_writes_evidence_the_finding_and_a_summary_then_moves_it_with_the_official_tool():
    w = writes(b.phase_4)
    assert {"itemdb/evidence/CC-0001/README.md", "itemdb/evidence/CC-0001/request.txt",
            "itemdb/evidence/CC-0001/response.txt"} <= set(w)
    assert b.FINDING_PATHS[0] in w
    # glob phase-4-CC-0001-summary*.md: the dated name is accepted here
    assert any(k.startswith("runs/phase-4-CC-0001-summary") for k in w)
    bash = [a["arguments"]["command"] for a in b.phase_4 if a.get("type") == "tool_call" and a["name"] == "bash"]
    assert bash[-1] == "python3 tools/move-finding.py CC-0001 CONFIRMED"


def test_mock_evidence_says_it_is_mock():
    # Fabricated evidence must never be mistaken for a real run.
    assert "not produced by a real run" in writes(b.phase_4)["itemdb/evidence/CC-0001/README.md"]
    assert "mock evidence" in b.finding_md(b.VULNS[0], validated=True).lower()


# ---- phase 5: what CodeCome requires of an EXPLOITED finding (tools/findings/checks.py, constants.py) ----
EXPLOITED_SECTIONS = ["Root cause analysis", "Data flow", "Inputs and preconditions", "Recording", "Remediation idea"]
EXPLOITATION_FIELDS = ["status", "impact_demonstrated", "exploit_type", "severity_before", "severity_after",
                       "artifacts_dir", "summary"]


def test_exploited_finding_has_the_exploitation_block_and_sections_codecome_requires():
    import yaml
    text = b.exploited_md(b.VULNS[0])
    front = yaml.safe_load(text.split("---")[1])
    # CodeCome's move tool only flips status/confidence: the agent writes everything else beforehand.
    assert front["status"] == "CONFIRMED" and front["confidence"] == "CONFIRMED"
    assert front["validation"]["status"] == "CONFIRMED"
    ex = front["exploitation"]
    assert all(k in ex for k in EXPLOITATION_FIELDS) and ex["status"] == "DEMONSTRATED"
    assert ex["impact_demonstrated"] and ex["exploit_type"] and front["cwe"]
    assert all(s.lower() in h1(text) for s in EXPLOITED_SECTIONS)
    assert "```diff" in text


def test_phase5_writes_artifacts_in_the_exploits_dir_updates_the_confirmed_file_then_moves_it():
    w = writes(b.phase_5)
    assert {"itemdb/evidence/CC-0001/exploits/README.md", "itemdb/evidence/CC-0001/exploits/poc-request.txt"} <= set(w)
    assert b.CONFIRMED_PATH in w and "/CONFIRMED/" in b.CONFIRMED_PATH
    assert any(k.startswith("runs/phase-5-CC-0001-summary") for k in w)
    bash = [a["arguments"]["command"] for a in b.phase_5 if a.get("type") == "tool_call" and a["name"] == "bash"]
    assert bash[-1] == "python3 tools/move-finding.py CC-0001 EXPLOITED"


def test_phase5_never_runs_anything_against_a_target():
    # The "exploit" is documentation only; every bash step in the scenarios is an echo or an official CodeCome tool.
    allowed = ("echo ", "python3 tools/move-finding.py", "python3 tools/check-frontmatter.py", "python3 tools/render-report.py",
               "rtk python3 tools/codecome.py")
    for name, script in b.SCENARIOS.items():
        for a in script:
            if a.get("type") == "tool_call" and a["name"] == "bash":
                assert a["arguments"]["command"].startswith(allowed), (name, a["arguments"]["command"])
    assert "not produced by a real run" in writes(b.phase_5)["itemdb/evidence/CC-0001/exploits/README.md"]


def test_phase6_renders_the_report_with_codecomes_own_tool():
    bash = [a["arguments"]["command"] for a in b.phase_6 if a.get("type") == "tool_call" and a["name"] == "bash"]
    assert bash == ["python3 tools/render-report.py"]
    assert any(k.startswith("runs/phase-6-summary") for k in writes(b.phase_6))
    assert "itemdb/reports/report.md" not in writes(b.phase_6)  # the tool writes it, not the script


def test_every_phase_has_a_distinct_title_and_the_set_is_complete():
    titles = list(b.SCENARIOS)
    assert len(titles) == 8 and len(set(titles)) == 8  # 1a 1b 1c 2 3 4 5 6
    for k in titles:
        assert [x for x in titles if x in k] == [k]
