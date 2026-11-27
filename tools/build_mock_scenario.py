#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Builds config/mock/phase1.json: mock scripts for sub-phases 1a, 1b and 1c over the tiny target.

Usage:  python tools/build_mock_scenario.py [output.json]
Each sub-phase opens a new session, so each has its own script, picked by the prompt's title.
"""
import json
import sys
from pathlib import Path

N = [0]


def _id():
    N[0] += 1
    return f"call_{N[0]}"


R = lambda p: {"type": "tool_call", "id": _id(), "name": "read", "arguments": {"filePath": p}}
W = lambda p, c: {"type": "tool_call", "id": _id(), "name": "write", "arguments": {"filePath": p, "content": c}}
T = lambda s, r=14: {"type": "text", "content": s, "tokens_per_second": r}
U = lambda n: {"type": "usage", "prompt_tokens": n}
S = lambda n: {"type": "sleep", "seconds": n}
DONE = {"type": "done"}


def both_summaries(phase, text):
    """The prompt asks for `runs/phase-<x>-summary-<date>.md`, but the 1b and 1c checks look for
    `runs/phase-<x>-summary.md` (no date). An agent faithful to the prompt would fail: both are written."""
    return [W(f"runs/phase-{phase}-summary-2026-11-24-000001.md", text), W(f"runs/phase-{phase}-summary.md", text)]

SUMMARY = """# CodeCome Run Summary

# Goal

{goal}

# Files read

{read}

# Files created

{created}

# Important observations

Mock run on the tiny demo target.

# Assumptions

None.

# Open questions for the user

None.

# Re-run prompt hints

None.
"""

# ---------------------------------------------------------------- 1a
TARGET_PROFILE = """# Target Reconnaissance Notes

Date: 2026-11-24
Phase: reconnaissance
Target path: `./src`

# Executive summary

Small Python web service built on the standard library (`http.server`). Three routes read user input
from the query string: `/user`, `/ping`, `/file`.

# Target profile

## Detected target type

Detected type: web application

Confidence: HIGH

## Languages

Python (`app.py`).

## Frameworks and technologies

Standard library only: `http.server`, `sqlite3`, `subprocess`.

## Repository structure

- `app.py`: HTTP handler and routes
- `public/`: static files served by `/file`
- `requirements.txt`: no third-party dependencies
"""
BUILD_MODEL = """# Build model

- **Build system**: none (interpreted).
- **Build commands**: none. Run with `python3 app.py`.
- **Dependencies**: standard library only (`requirements.txt` is empty).
- **Build prerequisites**: Python 3.
- **Can the target be built in the workspace**: yes, there is nothing to build.
"""
CODEQL_PLAN = """schema_version: 2
generated_by: "phase-1a-profile"

source_path: "./src"
recommended: true

analysis_units:
  - id: "app"
    path: "./src"
    kind: "service"
    primary: true
    sandbox_build_target: "root"
    languages:
      - id: "python"
        confidence: "HIGH"
        build_mode: "none"
        build_provider: "none"
        packs:
          - "official"
"""

phase_1a = [
    U(14135), S(14),
    T("I will start by reading the required reference files and then explore the source tree."),
    R("AGENTS.md"), R("codecome.yml"), R("templates/target-recon.md"),
    R("templates/codeql-plan.yml"), R(".opencode/agents/recon.md"), R(".opencode/skills/source-recon/SKILL.md"),
    U(31765), S(18),
    T("Now let me explore the source tree under src/ to understand its structure."),
    {"type": "tool_call", "id": _id(), "name": "glob", "arguments": {"pattern": "**/*", "path": "src"}},
    U(32100), S(4),
    T("The target is small. I will read the entry point and the dependency file."),
    R("src/app.py"), R("src/requirements.txt"),
    U(33200), S(4),
    T("Writing the three Phase 1a notes and the run summary."),
    W("itemdb/notes/target-profile.md", TARGET_PROFILE), W("itemdb/notes/build-model.md", BUILD_MODEL),
    W("itemdb/notes/codeql-plan.yml", CODEQL_PLAN),
    W("runs/phase-1a-summary-2026-11-24-000000.md", SUMMARY.format(
        goal="Phase 1a: target profile (mock run).", read="- AGENTS.md\n- codecome.yml\n- src/app.py",
        created="- itemdb/notes/target-profile.md\n- itemdb/notes/build-model.md\n- itemdb/notes/codeql-plan.yml")),
    U(34000), S(3),
    T("Validating the CodeQL plan."),
    {"type": "tool_call", "id": _id(), "name": "bash",
     "arguments": {"command": "rtk python3 tools/codecome.py check-codeql-plan", "description": "Validate the CodeQL plan"}},
    U(34400),
    T("Phase 1a is complete: target profile, build model and CodeQL plan are written."),
    DONE,
]

# ---------------------------------------------------------------- 1b
SANDBOX_PLAN = """# Sandbox plan

The target is a single Python script with no third-party dependencies, so no build step is needed.

- **Base image**: `python:3-slim` would be enough to run it.
- **Network**: none required to run the target.
- **Build target `root`**: nothing to compile.
- **Status**: not generated in this mock run (no Docker build was performed).
"""
phase_1b = [
    U(15200), S(8),
    T("I will read the Phase 1a notes before planning the sandbox."),
    R("itemdb/notes/target-profile.md"), R("itemdb/notes/build-model.md"),
    U(18900), S(4),
    T("The target needs no build, so the sandbox plan is minimal. Writing the plan and the summary."),
    W("itemdb/notes/sandbox-plan.md", SANDBOX_PLAN),
    *both_summaries("1b", SUMMARY.format(
        goal="Phase 1b: sandbox bootstrap (mock run).", read="- itemdb/notes/target-profile.md\n- itemdb/notes/build-model.md",
        created="- itemdb/notes/sandbox-plan.md")),
    U(19500),
    T("Phase 1b is complete: the sandbox plan is written."),
    DONE,
]

# ---------------------------------------------------------------- 1c
def note(title, body):
    return f"# {title}\n\n{body}\n"

THREAT_MODEL = "\n".join(f"# {h}\n\n{b}\n" for h, b in [
    ("Threat Model Summary", "A tiny HTTP service whose three routes pass query-string input to dangerous operations."),
    ("Scope", "`src/app.py` and the static files under `src/public/`."),
    ("System model", "Single process, `http.server`, in-memory SQLite, `subprocess` with a shell."),
    ("Assets and security objectives", "Confidentiality of the `users` table; integrity of the host; files outside `public/`."),
    ("Attacker model", "Any unauthenticated network client that can reach the listening port."),
    ("Trust boundary summary", "The query string crosses from the network into SQL, a shell command and a file path."),
    ("Existing controls", "None: no authentication, validation or escaping."),
    ("Abuse-path themes for Phase 2", "SQL injection, OS command injection, path traversal."),
    ("Risk calibration for review focus", "Everything in `app.py` is high interest; `public/index.html` is static."),
    ("Open questions for the user", "None."),
    ("Re-run prompt hints", "None."),
])
RISK_INDEX = """schema_version: 1
generated_by: "phase-1-recon"
generated_at: "2026-11-24T15:40:00Z"
target: "demo-src"
files:
  - path: "src/app.py"
    score: 5
    reasons:
      - "network entry point"
      - "query string reaches SQL, a shell and the filesystem"
  - path: "src/public/index.html"
    score: 1
    reasons:
      - "static content"
"""
phase_1c_notes = {
    "attack-surface.md": note("Attack surface", "- `/user?name=`\n- `/ping?host=`\n- `/file?name=`"),
    "execution-model.md": note("Execution model", "One process; each request handled synchronously by `Handler.do_GET`."),
    "trust-boundaries.md": note("Trust boundaries", "Network client -> HTTP handler -> SQLite / shell / filesystem."),
    "data-flow.md": note("Data flow", "`parse_qs(url.query)` -> string formatting / concatenation -> sink."),
    "validation-model.md": note("Validation model", "Each route can be exercised with a single HTTP request."),
    "interesting-files.md": note("Interesting files", "- `src/app.py`: all the logic.\n- `src/public/index.html`: static."),
    "security-assumptions.md": note("Security assumptions", "The service is reachable by untrusted clients."),
}
phase_1c = [
    U(16000), S(8),
    T("I will read the previous notes and the entry point before writing the reconnaissance notes."),
    R("itemdb/notes/target-profile.md"), R("itemdb/notes/sandbox-plan.md"), R("src/app.py"),
    U(24000), S(4),
    T("Writing the reconnaissance notes, the threat model and the file risk index."),
    *[W(f"itemdb/notes/{n}", c) for n, c in phase_1c_notes.items()],
    W("itemdb/notes/file-risk-index.yml", RISK_INDEX),
    W("itemdb/notes/threat-model.md", THREAT_MODEL),
    *both_summaries("1c", SUMMARY.format(
        goal="Phase 1c: detailed reconnaissance (mock run).", read="- src/app.py",
        created="- itemdb/notes/threat-model.md\n- itemdb/notes/file-risk-index.yml")),
    U(26000),
    T("Phase 1c is complete: reconnaissance notes, threat model and risk index are written."),
    DONE,
]


# ---------------------------------------------------------------- findings (phases 2-6)
# Three deliberately obvious vulnerabilities in the demo target (see tests/fixtures/DEMO-EXPECTED.md).
VULNS = [
    dict(id="CC-0001", slug="sql-injection-user-name", title="SQL injection in /user via the name parameter",
         severity="HIGH", category="SQL injection", cwe="CWE-89", entry="GET /user?name=",
         source="query string parameter 'name'", sink="sqlite3 execute() with string formatting",
         asset="users table (names and secrets)", line="rows = DB.execute(\"SELECT id, name FROM users WHERE name = '%s'\" % name)",
         attack="name=' OR '1'='1", impact="Read every row of the users table, including the secrets."),
    dict(id="CC-0002", slug="os-command-injection-ping", title="OS command injection in /ping via the host parameter",
         severity="CRITICAL", category="OS command injection", cwe="CWE-78", entry="GET /ping?host=",
         source="query string parameter 'host'", sink="subprocess.run(..., shell=True)",
         asset="the host running the service", line="result = subprocess.run(\"ping -c 1 \" + host, shell=True, capture_output=True, text=True)",
         attack="host=127.0.0.1;id", impact="Run arbitrary commands with the privileges of the service."),
    dict(id="CC-0003", slug="path-traversal-file-name", title="Path traversal in /file via the name parameter",
         severity="MEDIUM", category="Path traversal", cwe="CWE-22", entry="GET /file?name=",
         source="query string parameter 'name'", sink="open(os.path.join(PUBLIC_DIR, name))",
         asset="files readable by the service account", line="with open(os.path.join(PUBLIC_DIR, name)) as handle:",
         attack="name=../app.py", impact="Read any file the service account can read."),
]


# CVSS v4 data and a fix for each finding. The scores sit inside the band CodeCome requires for the stated severity
# (MEDIUM 4.0-6.9, HIGH 7.0-8.9, CRITICAL 9.0-10.0); they are illustrative, not computed.
CVSS = {
    "CC-0001": ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:N/VA:N/SC:N/SI:N/SA:N", 8.7,
                "Network reachable, no authentication, full read of the users table."),
    "CC-0002": ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N", 9.3,
                "Network reachable, no authentication, arbitrary command execution."),
    "CC-0003": ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:L/VI:N/VA:N/SC:N/SI:N/SA:N", 6.9,
                "Network reachable, no authentication, read of files the service can read."),
}
FIX = {
    "CC-0001": "- rows = DB.execute(\"SELECT id, name FROM users WHERE name = '%s'\" % name).fetchall()\n"
               "+ rows = DB.execute(\"SELECT id, name FROM users WHERE name = ?\", (name,)).fetchall()",
    "CC-0002": "- result = subprocess.run(\"ping -c 1 \" + host, shell=True, capture_output=True, text=True)\n"
               "+ result = subprocess.run([\"ping\", \"-c\", \"1\", host], capture_output=True, text=True)",
    "CC-0003": "- with open(os.path.join(PUBLIC_DIR, name)) as handle:\n"
               "+ path = os.path.realpath(os.path.join(PUBLIC_DIR, name))\n"
               "+ if not path.startswith(PUBLIC_DIR + os.sep): return self._send(403, \"forbidden\")\n"
               "+ with open(path) as handle:",
}


def finding_md(v, reviewed=False, validated=False):
    """A finding as CodeCome expects it at each stage.

    default      -> after Phase 2: PENDING, passes the Phase 2 quality check (frontmatter + 11 sections).
    reviewed     -> after Phase 3: only the counter-analysis text changes.
    validated    -> what the Phase 4 agent writes BEFORE moving the file: CVSS, methods, evidence and a remediation
                    idea are filled in, but `status` is still PENDING. `tools/move-finding.py CC-XXXX CONFIRMED`
                    then flips status/confidence/validation.status and moves the file, exactly as in a real run.
    """
    counter = ("Phase 3 review: looked for validation, escaping or an allow-list on the path from the source to the sink "
               "and found none. Not a duplicate of the other two findings (different sink and root cause). Kept as PENDING."
               if reviewed or validated else
               "First pass: no validation, escaping or allow-list was found between the source and the sink.")
    vec, score, why = CVSS[v["id"]] if validated else ("", 0.0, "")
    methods = '\n  - "http_exploit"' if validated else " []"
    vsummary = ("Reproduced with a single request against the sandboxed service (mock evidence)." if validated else "")
    result = ("Confirmed: the request below returns data the caller should not see (mock evidence, not a real run)."
              if validated else "Not validated yet.")
    evidence = (f"`itemdb/evidence/{v['id']}/request.txt` and `response.txt`." if validated else "None yet.")
    remediation = (f"\n# Remediation idea\n\nPass the value as data, never as part of the code or command:\n\n```diff\n{FIX[v['id']]}\n```\n"
                   if validated else "")
    return f"""---
id: "{v['id']}"
title: "{v['title']}"
status: "PENDING"
severity: "{v['severity']}"
cvss_v4:
  vector: "{vec}"
  score: {score}
  justification: "{why}"
confidence: "MEDIUM"
category: "{v['category']}"
cwe:
  - "{v['cwe']}"
language: "python"
target_area: "HTTP handler {v['entry'].split()[1]}"
files:
  - "src/app.py"
symbols:
  - "Handler.do_GET"
entry_points:
  - "{v['entry']}"
sources:
  - "{v['source']}"
sinks:
  - "{v['sink']}"
trust_boundary: "network client to the service host"
assets_at_risk:
  - "{v['asset']}"
validation:
  status: "NOT_STARTED"
  methods:{methods}
  evidence_dir: "itemdb/evidence/{v['id']}"
  summary: "{vsummary}"
exploitation:
  status: "NOT_STARTED"
  impact_demonstrated: ""
  exploit_type: ""
  severity_before: ""
  severity_after: ""
  artifacts_dir: "itemdb/evidence/{v['id']}/exploits"
  summary: ""
created_at: "2026-11-24"
updated_at: "2026-11-24"
---

# Summary

The `{v['entry'].split()[1]}` route passes the {v['source']} to {v['sink']} without validation.

# Target context

Small standard-library Python web service; the route is reachable by any unauthenticated network client.

# Affected code

`src/app.py`, `Handler.do_GET`:

    {v['line']}

# Vulnerability hypothesis

The input is attacker-controlled and reaches a dangerous operation unchanged. This is known from reading the code; the
exact behaviour at runtime is assumed until Phase 4 reproduces it.

# Source-to-sink reasoning

`parse_qs(url.query)` returns the raw parameter; the value is used directly in the sink on the next lines.

# Attackability / trigger conditions

A single GET request, for example `{v['attack']}`. No authentication or special state is needed.

# Impact

{v['impact']}

# Validation plan

Start the service in the sandbox and send the request above; compare the response with a benign request.

# Counter-analysis

{counter}

# Validation result

{result}

# Evidence

{evidence}
{remediation}"""


def summary_md(goal, read, created):
    return SUMMARY.format(goal=goal, read=read, created=created)


FINDING_PATHS = [f"itemdb/findings/PENDING/{v['id']}-{v['slug']}.md" for v in VULNS]
READ_NOTES = [R("itemdb/notes/target-profile.md"), R("itemdb/notes/threat-model.md"), R("itemdb/notes/file-risk-index.yml")]

phase_2 = [
    U(22000), S(10),
    T("I will read the reconnaissance notes and the target before forming hypotheses."),
    R("AGENTS.md"), R("templates/finding.md"), *READ_NOTES, R("src/app.py"),
    U(30000), S(6),
    T("Three routes pass query-string input to dangerous operations. Writing one finding for each."),
    *[W(path, finding_md(v)) for path, v in zip(FINDING_PATHS, VULNS)],
    W("runs/phase-2-summary-2026-11-24-000003.md", summary_md(
        "Phase 2: hypothesis generation (mock run).", "- src/app.py\n- itemdb/notes/threat-model.md",
        "\n".join(f"- {p}" for p in FINDING_PATHS))),
    U(33000), S(3),
    T("Checking that the findings are well formed."),
    {"type": "tool_call", "id": _id(), "name": "bash",
     "arguments": {"command": "python3 tools/check-frontmatter.py", "description": "Validate finding frontmatter"}},
    U(33500),
    T("Phase 2 is complete: three candidate findings were created in PENDING."),
    DONE,
]

phase_3 = [
    U(24000), S(8),
    T("I will re-read each candidate finding and try to disprove or deduplicate it."),
    *[R(p) for p in FINDING_PATHS], R("itemdb/notes/threat-model.md"),
    U(30000), S(5),
    T("None of the three is a duplicate and none can be disproved from the code. Recording the counter-analysis."),
    *[W(path, finding_md(v, reviewed=True)) for path, v in zip(FINDING_PATHS, VULNS)],
    W("runs/phase-3-summary-2026-11-24-000004.md", summary_md(
        "Phase 3: counter-analysis and deduplication (mock run).", "\n".join(f"- {p}" for p in FINDING_PATHS),
        "(findings updated in place)")),
    U(33000),
    T("Phase 3 is complete: three findings reviewed, none rejected or merged."),
    DONE,
]


# Phase 4 validates ONE finding. The prompt carries the id through a placeholder substitution and also contains example
# ids, so the mock cannot tell which finding was requested: this scenario always validates CC-0001.
V0 = VULNS[0]
EVID = f"itemdb/evidence/{V0['id']}"
phase_4 = [
    U(26000), S(8),
    T(f"I will validate {V0['id']}: read the finding, the code and the validation skill first."),
    R("AGENTS.md"), R(FINDING_PATHS[0]), R("src/app.py"), R(".opencode/skills/exploit-validation/SKILL.md"),
    U(31000), S(5),
    T("Reproducing the issue against the sandboxed service and recording the evidence."),
    {"type": "tool_call", "id": _id(), "name": "bash",
     "arguments": {"command": "echo 'mock reproduction step (nothing is executed against the target)'", "description": "Mock reproduction"}},
    W(f"{EVID}/README.md", f"# Evidence for {V0['id']}\n\n# Summary\n\nMock evidence written by the test LLM; it was not produced by a real run.\n\n"
                           "# Validation method\n\n- HTTP exploit\n"),
    W(f"{EVID}/request.txt", f"GET /user?name={V0['attack'].split('=', 1)[1]} HTTP/1.1\nHost: sandbox\n"),
    W(f"{EVID}/response.txt", "HTTP/1.0 200 OK\n\n[(1, 'alice'), (2, 'bob')]\n"),
    W(FINDING_PATHS[0], finding_md(V0, validated=True)),
    W(f"runs/phase-4-{V0['id']}-summary-2026-11-24-000005.md", summary_md(
        f"Phase 4: validation of {V0['id']} (mock run).", f"- {FINDING_PATHS[0]}\n- src/app.py",
        f"- {EVID}/README.md\n- {EVID}/request.txt\n- {EVID}/response.txt")),
    U(34000), S(3),
    T("Evidence is in place. Confirming the finding with the official tool."),
    {"type": "tool_call", "id": _id(), "name": "bash",
     "arguments": {"command": f"python3 tools/move-finding.py {V0['id']} CONFIRMED", "description": "Move the finding to CONFIRMED"}},
    U(34500),
    T(f"Phase 4 is complete: {V0['id']} is CONFIRMED."),
    DONE,
]


def exploited_md(v):
    """The finding as the Phase 5 agent leaves it BEFORE `move-finding.py ... EXPLOITED` runs.

    Starts from the validated finding, applies what the Phase 4 move already did (status, confidence and
    validation.status = CONFIRMED), then fills the exploitation block and the four sections CodeCome requires for
    EXPLOITED (tools/findings/constants.py: REQUIRED_EXPLOITED_SECTIONS). The tool only flips status/confidence.
    """
    text = finding_md(v, validated=True)
    text = text.replace('status: "PENDING"', 'status: "CONFIRMED"', 1)
    text = text.replace('confidence: "MEDIUM"', 'confidence: "CONFIRMED"', 1)
    text = text.replace('validation:\n  status: "NOT_STARTED"', 'validation:\n  status: "CONFIRMED"', 1)
    old = (f'exploitation:\n  status: "NOT_STARTED"\n  impact_demonstrated: ""\n  exploit_type: ""\n'
           f'  severity_before: ""\n  severity_after: ""\n  artifacts_dir: "itemdb/evidence/{v["id"]}/exploits"\n  summary: ""')
    assert old in text, "exploitation block changed: update exploited_md"
    new = (f'exploitation:\n  status: "DEMONSTRATED"\n  impact_demonstrated: "{v["impact"]} (mock demonstration)"\n'
           f'  exploit_type: "http_exploit"\n  severity_before: "{v["severity"]}"\n  severity_after: "{v["severity"]}"\n'
           f'  artifacts_dir: "itemdb/evidence/{v["id"]}/exploits"\n  summary: "Single unauthenticated request (mock, not a real run)."')
    text = text.replace(old, new, 1)
    extra = f"""
# Exploitation Result

Demonstrated with one unauthenticated HTTP request (mock result: nothing was executed against the target).

# Demonstrated Impact

{v['impact']}

# Root cause analysis

The request parameter is concatenated into the dangerous operation instead of being passed as data.

# Data flow

`parse_qs(url.query)` -> local variable -> {v['sink']}.

# Inputs and preconditions

Network access to the service. No authentication, no special state.

# Recording

Mock run: no terminal recording was produced.
"""
    return text.replace("\n# Remediation idea", extra + "\n# Remediation idea", 1)


CONFIRMED_PATH = FINDING_PATHS[0].replace("/PENDING/", "/CONFIRMED/")
XDIR = f"{EVID}/exploits"
phase_5 = [
    U(28000), S(8),
    T(f"I will develop a proof of concept for {V0['id']}, which Phase 4 confirmed."),
    R("AGENTS.md"), R(CONFIRMED_PATH), R(f"{EVID}/README.md"), R("src/app.py"),
    R("templates/exploit-readme.md"), R(".opencode/skills/exploit-development/SKILL.md"),
    U(33000), S(5),
    T("The issue is reproducible with a single request. Documenting the proof of concept and updating the finding."),
    {"type": "tool_call", "id": _id(), "name": "bash",
     "arguments": {"command": "echo 'mock proof of concept step (nothing is executed against the target)'", "description": "Mock PoC step"}},
    W(f"{XDIR}/README.md", f"# Exploit for {V0['id']}\n\n# Summary\n\nMock artifact written by the test LLM; it was not produced by a real run.\n\n"
                           f"# Proof of concept\n\nOne HTTP request: `GET /user?name={V0['attack'].split('=', 1)[1]}`.\n"),
    W(f"{XDIR}/poc-request.txt", f"GET /user?name={V0['attack'].split('=', 1)[1]} HTTP/1.1\nHost: sandbox\n"),
    W(CONFIRMED_PATH, exploited_md(V0)),
    W(f"runs/phase-5-{V0['id']}-summary-2026-11-24-000006.md", summary_md(
        f"Phase 5: exploit development for {V0['id']} (mock run).", f"- {CONFIRMED_PATH}\n- src/app.py",
        f"- {XDIR}/README.md\n- {XDIR}/poc-request.txt")),
    U(35000), S(3),
    T("Artifacts are in place. Marking the finding as exploited with the official tool."),
    {"type": "tool_call", "id": _id(), "name": "bash",
     "arguments": {"command": f"python3 tools/move-finding.py {V0['id']} EXPLOITED", "description": "Move the finding to EXPLOITED"}},
    U(35500),
    T(f"Phase 5 is complete: {V0['id']} is EXPLOITED."),
    DONE,
]


# Phase 6: the report is rendered by CodeCome's own tool from the findings on disk, so nothing in it is scripted by us.
EXPLOITED_PATH = FINDING_PATHS[0].replace("/PENDING/", "/EXPLOITED/")
phase_6 = [
    U(20000), S(6),
    T("I will read the report template and the findings that reached CONFIRMED or EXPLOITED."),
    R("AGENTS.md"), R("templates/report.md"), R(EXPLOITED_PATH),
    U(26000), S(3),
    T("Rendering the report with the official tool."),
    {"type": "tool_call", "id": _id(), "name": "bash",
     "arguments": {"command": "python3 tools/render-report.py", "description": "Render the report from the findings"}},
    W("runs/phase-6-summary-2026-11-24-000007.md", summary_md(
        "Phase 6: reporting (mock run).", f"- templates/report.md\n- {EXPLOITED_PATH}", "- itemdb/reports/report.md")),
    U(27000),
    T("Phase 6 is complete: the report was rendered from the findings."),
    DONE,
]

SCENARIOS = {
    "codecome phase 1a: target profile": phase_1a,
    "codecome phase 1b: sandbox bootstrap": phase_1b,
    "codecome phase 1c: detailed reconnaissance": phase_1c,
    "codecome phase 2: vulnerability hypothesis generation": phase_2,
    "codecome phase 3: counter-analysis and deduplication": phase_3,
    "codecome phase 4: finding validation": phase_4,
    "codecome phase 5: exploit development": phase_5,
    "codecome phase 6: reporting": phase_6,
}


def build() -> dict:
    return {"scenarios": [{"match": m, "script": s} for m, s in SCENARIOS.items()],
            "default": [T("No mock scenario is written for this phase yet."), DONE]}


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "config" / "mock" / "phase1.json")
    out.write_text(json.dumps(build(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{out}: " + ", ".join(f"{m.split(':')[0]}={len(s)} acciones" for m, s in SCENARIOS.items()))
