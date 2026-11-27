# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
import os

from app import audit, phases


def put(root, rel, text="x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def finding(root, status, fid, sev="HIGH", validation="NOT_STARTED"):
    put(root, f"itemdb/findings/{status}/{fid}-x.md",
        f'---\nid: "{fid}"\ntitle: "t {fid}"\nseverity: "{sev}"\nvalidation:\n  status: "{validation}"\n---\nbody\n')


def phase1a(r):
    for n in ("target-profile.md", "build-model.md", "codeql-plan.yml"):
        put(r, f"itemdb/notes/{n}")
    put(r, "runs/phase-1a-summary-2026.md")


def phase1b(r):
    put(r, "itemdb/notes/sandbox-plan.md")
    put(r, "runs/phase-1b-summary.md")


def phase1c(r):
    for n in phases.NOTES_1C:
        put(r, f"itemdb/notes/{n}")
    put(r, "runs/phase-1c-summary.md")


def phase2(r):
    for i in (1, 2, 3):
        finding(r, "PENDING", f"CC-000{i}")
    put(r, "runs/phase-2-summary-x.md")


def phase4(r, fid="CC-0001"):
    os.remove(next((r / "itemdb/findings/PENDING").glob(f"{fid}-*.md")))
    finding(r, "CONFIRMED", fid, validation="CONFIRMED")
    put(r, f"itemdb/evidence/{fid}/README.md")


def complete(r):
    phase1a(r); phase1b(r); phase1c(r); phase2(r); phase4(r)
    put(r, "runs/phase-3-summary-x.md")
    os.remove(next((r / "itemdb/findings/CONFIRMED").glob("CC-0001-*.md")))
    finding(r, "EXPLOITED", "CC-0001", validation="CONFIRMED")
    put(r, "itemdb/evidence/CC-0001/exploits/poc.py")
    put(r, "itemdb/reports/report.md")


def job(i, action, status, finding=None):
    return {"id": i, "action": action, "finding": finding, "status": status}


def st(d):
    out = {}
    for p in d["phases"]:
        out[p["key"]] = p["status"]
        for s in p.get("steps", []):
            out[s["key"]] = s["status"]
    return out


def test_empty_folder(tmp_path):
    d = audit.derive(tmp_path, [])
    assert set(st(d).values()) == {"none"}
    assert [p["key"] for p in d["phases"]] == ["1", "2", "3", "4", "5", "6"]
    assert d["matrix"] == [] and d["phases_done"] == 0 and d["phases_total"] == 6
    assert d["phases"][0]["steps"][1]["needs_unmet"] == ["1a"]


def test_after_1a(tmp_path):
    phase1a(tmp_path)
    s = st(audit.derive(tmp_path, []))
    assert (s["1a"], s["1b"], s["1c"], s["1"]) == ("done", "none", "none", "none")
    a = audit.derive(tmp_path, [])["phases"][0]["steps"][0]
    assert a["artifacts"] == {"found": 3, "expected": 3, "summary": True}
    assert a["external"] is True  # no web job explains it


def test_after_1c(tmp_path):
    phase1a(tmp_path); phase1b(tmp_path); phase1c(tmp_path)
    jobs = [job(1, "phase-1", "done")]
    d = audit.derive(tmp_path, jobs)
    s = st(d)
    assert (s["1"], s["2"]) == ("done", "none")
    assert d["phases"][0]["steps"][2]["external"] is False
    assert d["phases"][1]["needs_unmet"] == []


def test_after_2(tmp_path):
    phase1a(tmp_path); phase1b(tmp_path); phase1c(tmp_path); phase2(tmp_path)
    d = audit.derive(tmp_path, [])
    assert st(d)["2"] == "done" and st(d)["3"] == "none" and st(d)["4"] == "none"
    assert [r["id"] for r in d["matrix"]] == ["CC-0001", "CC-0002", "CC-0003"]
    assert d["findings_by_status"]["PENDING"] == 3


def test_after_4(tmp_path):
    phase1a(tmp_path); phase1b(tmp_path); phase1c(tmp_path); phase2(tmp_path); phase4(tmp_path)
    d = audit.derive(tmp_path, [])
    row = d["matrix"][0]
    assert (row["p4"]["status"], row["p5"]["status"]) == ("done", "none")
    p4 = next(p for p in d["phases"] if p["key"] == "4")
    assert p4["status"] == "partial" and p4["status_counts"] == {"done": 1, "none": 2}


def test_complete(tmp_path):
    complete(tmp_path)
    for fid in ("CC-0002", "CC-0003"):  # exploit the other two as well
        os.remove(next((tmp_path / "itemdb/findings/PENDING").glob(f"{fid}-*.md")))
        finding(tmp_path, "EXPLOITED", fid, validation="CONFIRMED")
        put(tmp_path, f"itemdb/evidence/{fid}/exploits/poc.py")
    d = audit.derive(tmp_path, [])
    s = st(d)
    assert all(s[k] == "done" for k in ("1", "2", "3", "4", "5", "6"))
    assert d["phases_done"] == 6


def test_partly_exploited_is_not_phase_done(tmp_path):
    complete(tmp_path)  # CC-0002 and CC-0003 are still PENDING
    d = audit.derive(tmp_path, [])
    assert st(d)["5"] == "partial" and d["matrix"][0]["p5"]["status"] == "done"


def test_precedence_running_over_done(tmp_path):
    phase1a(tmp_path); phase1b(tmp_path); phase1c(tmp_path)
    d = audit.derive(tmp_path, [job(1, "phase-1", "done"), job(2, "phase-1", "running")])
    assert d["phases"][0]["status"] == "running"  # a re-run shows as running


def test_precedence_done_over_failed(tmp_path):
    phase1a(tmp_path)
    jobs = [job(1, "phase-1", "failed")]
    s = st(audit.derive(tmp_path, jobs))
    assert s["1a"] == "done" and s["1b"] == "failed"  # failed lands on the first missing step


def test_phase1_running_marks_first_missing_substep(tmp_path):
    phase1a(tmp_path)
    s = st(audit.derive(tmp_path, [job(1, "phase-1", "running")]))
    assert (s["1a"], s["1b"], s["1c"], s["1"]) == ("done", "running", "none", "running")


def test_queued_and_cancelled_and_partial_artifacts(tmp_path):
    put(tmp_path, "itemdb/notes/target-profile.md")
    d = audit.derive(tmp_path, [job(1, "phase-1", "cancelled")])
    a = d["phases"][0]["steps"][0]
    assert a["status"] == "failed" and a["failure"] == "cancelled"
    assert a["artifacts"]["found"] == 1 and a["artifacts"]["expected"] == 3
    assert st(audit.derive(tmp_path, [job(2, "phase-1", "queued")]))["1a"] == "queued"


def test_only_last_job_decides_failure(tmp_path):
    jobs = [job(1, "phase-2", "failed"), job(2, "phase-2", "done")]
    assert st(audit.derive(tmp_path, jobs))["2"] == "none"  # last one finished but disk is empty


def test_finding_cell_jobs_are_per_finding(tmp_path):
    phase2(tmp_path)
    jobs = [job(1, "phase-4", "running", "CC-0002")]
    d = audit.derive(tmp_path, jobs)
    cells = {r["id"]: r["p4"]["status"] for r in d["matrix"]}
    assert cells == {"CC-0001": "none", "CC-0002": "running", "CC-0003": "none"}
    assert next(p for p in d["phases"] if p["key"] == "4")["status"] == "running"


def test_rejected_by_validation_counts_as_done_and_is_discarded(tmp_path):
    finding(tmp_path, "REJECTED", "CC-0001", validation="REJECTED")
    put(tmp_path, "itemdb/evidence/CC-0001/README.md")
    finding(tmp_path, "DUPLICATE", "CC-0002")
    d = audit.derive(tmp_path, [])
    rows = {r["id"]: r for r in d["matrix"]}
    assert rows["CC-0001"]["p4"]["status"] == "done" and rows["CC-0001"]["discarded"]
    assert rows["CC-0002"]["p4"]["status"] == "none" and rows["CC-0002"]["discarded"]
    assert next(p for p in d["phases"] if p["key"] == "4")["status"] == "none"  # discarded rows excluded


def test_matrix_order_severity_then_id(tmp_path):
    finding(tmp_path, "PENDING", "CC-0001", sev="LOW")
    finding(tmp_path, "PENDING", "CC-0002", sev="CRITICAL")
    finding(tmp_path, "PENDING", "CC-0003", sev="LOW")
    assert [r["id"] for r in audit.derive(tmp_path, [])["matrix"]] == ["CC-0002", "CC-0001", "CC-0003"]


def test_symlinked_artifact_is_ignored(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("x")
    root = tmp_path / "ws"
    (root / "itemdb/reports").mkdir(parents=True)
    (root / "itemdb/reports/report.md").symlink_to(outside)
    assert st(audit.derive(root, []))["6"] == "none"


def test_pure_does_not_write(tmp_path):
    complete(tmp_path)
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    audit.derive(tmp_path, [])
    assert before == sorted(str(p) for p in tmp_path.rglob("*"))
