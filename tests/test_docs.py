# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
import json
import os

import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import Auth, hash_password
from app.docs import DocError, Docs, md_tree, read_safe
from tests.test_api import PW

GIF = b"GIF89a" + b"\x00" * 20


def flat(tree):
    return json.dumps(tree, ensure_ascii=False)


def test_raw_html_and_script_stay_text():
    tree = md_tree('<script>alert(1)</script>\n\nHi <img src=x onerror=alert(1)> **bold**')
    s = flat(tree)
    assert '"t": "script"' not in s and '"t": "img"' not in s
    assert "<script>alert(1)</script>" in s and "onerror" in s  # literal text, not elements
    assert {"t": "strong", "c": ["bold"]} in tree[1]["c"]


@pytest.mark.parametrize("href, kept", [
    ("https://example.com", True), ("#sec", True), ("itemdb/notes/a.md", True), ("./x", True),
    ("javascript:alert(1)", False), ("JaVaScRiPt:alert(1)", False), ("data:text/html,x", False),
    ("vbscript:x", False), ("//evil.example/x", False)])
def test_links_are_filtered(href, kept):
    s = flat(md_tree(f"[go]({href})"))
    assert ('"t": "a"' in s) is kept and "go" in s


def test_tables_code_and_images():
    tree = md_tree("| a | b |\n|:-:|--:|\n| 1 | `x<y` |\n\n```bash\nrm -rf /\n```\n\n![alt text](http://x/y.png)")
    s = flat(tree)
    assert '"t": "table"' in s and '"align": "center"' in s and '"align": "right"' in s
    assert {"t": "code", "c": ["x<y"]} in tree[0]["c"][1]["c"][0]["c"][1]["c"]
    assert {"t": "pre", "c": ["rm -rf /\n"]} in tree
    assert '"t": "img"' not in s and "alt text" in s


def test_read_safe_refuses_symlinks_fifos_and_big_files(tmp_path):
    secret = tmp_path / "secret"; secret.write_text("x")
    link = tmp_path / "link"; link.symlink_to(secret)
    with pytest.raises(DocError) as e:
        read_safe(link, 100)
    assert e.value.code == "doc_unreadable"
    fifo = tmp_path / "fifo"; os.mkfifo(fifo)
    with pytest.raises(DocError):
        read_safe(fifo, 100)  # must not block
    big = tmp_path / "big"; big.write_bytes(b"a" * 101)
    with pytest.raises(DocError) as e:
        read_safe(big, 100)
    assert e.value.code == "doc_too_large"
    with pytest.raises(DocError) as e:
        read_safe(tmp_path / "missing", 100)
    assert e.value.code == "doc_not_found"


def ws(tmp_path):
    root = tmp_path / "ws"
    (root / "itemdb" / "reports").mkdir(parents=True)
    (root / "itemdb" / "reports" / "report.md").write_text("# Report\n\n## CC-0001 — x\n\ntext\n")
    rec = root / "itemdb" / "evidence" / "CC-0001" / "exploits" / "recordings"
    rec.mkdir(parents=True)
    (rec / "exploit.gif").write_bytes(GIF)
    return root


def test_report_and_recordings(tmp_path):
    root = ws(tmp_path)
    bad = root / "itemdb" / "evidence" / "CC-0002" / "exploits" / "recordings"
    bad.mkdir(parents=True)
    (bad / "exploit.gif").write_bytes(b"<html>not a gif")
    (root / "itemdb" / "evidence" / "notanid").mkdir()
    d = Docs(root)
    r = d.report()
    assert r["tree"][0] == {"t": "h1", "c": ["Report"]} and "CC-0001" in r["recordings"]
    assert d.recording_gif("CC-0001") == GIF
    with pytest.raises(DocError) as e:
        d.recording_gif("CC-0002")
    assert e.value.code == "doc_not_gif"


def test_report_symlinked_out_of_the_workspace_is_refused(tmp_path):
    root = ws(tmp_path)
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "report.md").write_text("# secret")
    rep = root / "itemdb" / "reports"
    (rep / "report.md").unlink(); rep.rmdir(); rep.symlink_to(outside)
    with pytest.raises(DocError):
        Docs(root).report()


@pytest.fixture()
def api(monkeypatch, tmp_path):
    root = ws(tmp_path)
    monkeypatch.setattr(main, "docs", Docs(root))
    monkeypatch.setattr(main, "auth", Auth({"ana": hash_password(PW)}, b"k" * 32))
    return TestClient(main.app)


def test_api(api):
    assert api.get("/api/report").status_code == 401
    assert api.get("/api/findings/CC-0001/recording.gif").status_code == 401
    assert api.post("/api/login", json={"username": "ana", "password": PW}).status_code == 200
    assert api.get("/api/report").json()["recordings"] == ["CC-0001"]
    r = api.get("/api/findings/CC-0001/recording.gif")
    assert r.status_code == 200 and r.content == GIF
    assert r.headers["content-type"] == "image/gif" and r.headers["x-content-type-options"] == "nosniff"
    assert api.get("/api/findings/CC-9/recording.gif").status_code == 404
    assert api.get("/api/findings/x/recording.gif").status_code == 400


def test_no_report_yet_is_404(api, tmp_path):
    (tmp_path / "ws" / "itemdb" / "reports" / "report.md").unlink()
    api.post("/api/login", json={"username": "ana", "password": PW})
    r = api.get("/api/report")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "doc_not_found"


def test_finding_detail_carries_a_safe_tree_and_recording_flag(api, monkeypatch, tmp_path):
    from app.itemdb import ItemDB
    root = tmp_path / "ws"
    f = root / "itemdb" / "findings" / "EXPLOITED" / "CC-0001-x.md"
    f.parent.mkdir(parents=True)
    f.write_text('---\nid: "CC-0001"\ntitle: "x"\nstatus: "EXPLOITED"\n---\n\n# Summary\n\n<script>alert(1)</script> **bold**\n')
    monkeypatch.setattr(main, "db", ItemDB(root))
    api.post("/api/login", json={"username": "ana", "password": PW})
    d = api.get("/api/findings/CC-0001").json()
    assert d["has_recording"] is True and d["tree"][0] == {"t": "h1", "c": ["Summary"]}
    assert '"t": "script"' not in json.dumps(d["tree"]) and "<script>" in json.dumps(d["tree"])


# ---------- exploit recording (asciicast v2) ----------
from app.docs import CLEAR, ERASE_LINE, parse_cast  # noqa: E402


def cast(*events, head=None):
    h = head or {"version": 2, "width": 80, "height": 24, "idle_time_limit": 2.0, "title": "t<b>x</b>"}
    return (json.dumps(h) + "\n" + "\n".join(json.dumps(e) for e in events)).encode()


def test_cast_is_reduced_to_plain_text_and_markers():
    d = parse_cast(cast([0.1, "o", "\x1b[31mred\x1b[0m \x1b]0;window title\x07ok\r\n"],
                        [0.2, "o", "abc\x1b[K\x1b[2J\x1b[H\x00\x07zz\b\t"],
                        [0.3, "i", "typed"], [0.4, "m", ""], [0.5, "r", "90x30"]))
    assert d["events"][0][1] == "red ok\r\n"                    # colours and OSC titles dropped
    assert d["events"][1][1] == "abc" + ERASE_LINE + CLEAR + "zz\b\t"  # NUL/BEL dropped, markers kept
    assert len(d["events"]) == 2 and d["title"] == "t<b>x</b>"   # only output; title is plain text
    assert (d["width"], d["height"]) == (80, 24)


def test_cast_idle_time_is_compressed_and_bad_lines_skipped():
    d = parse_cast(cast([1.0, "o", "a"], [61.0, "o", "b"], ["x"], [62.0, "o", 5], [62.5, "o", "c"]) + b"\nnot json")
    assert [e[0] for e in d["events"]] == [1.0, 3.0, 4.5] and d["duration"] == 4.5  # 60 s idle -> 2 s


@pytest.mark.parametrize("head", [{"version": 1}, ["x"], "nope"])
def test_cast_needs_a_v2_header(head):
    with pytest.raises(DocError):
        parse_cast(cast([0, "o", "x"], head=head) if isinstance(head, (dict, list)) else b"nope\n")


def test_cast_absurd_sizes_are_clamped():
    d = parse_cast(cast([0, "o", "x"], head={"version": 2, "width": 99999, "height": -1}))
    assert (d["width"], d["height"]) == (100, 30)


def test_cast_api(api, tmp_path):
    rec = tmp_path / "ws" / "itemdb" / "evidence" / "CC-0001" / "exploits" / "recordings"
    (rec / "exploit.cast").write_bytes(cast([0.5, "o", "uid=0(root)\r\n"]))
    assert api.get("/api/findings/CC-0001/recording").status_code == 401
    api.post("/api/login", json={"username": "ana", "password": PW})
    d = api.get("/api/findings/CC-0001/recording").json()
    assert d["events"] == [[0.5, "uid=0(root)\r\n"]]
    assert api.get("/api/findings/CC-0002/recording").status_code == 404
    assert api.get("/api/findings/bad/recording").status_code == 400


def test_report_lists_findings_with_a_text_recording(api, tmp_path):
    rec = tmp_path / "ws" / "itemdb" / "evidence" / "CC-0001" / "exploits" / "recordings"
    assert api.post("/api/login", json={"username": "ana", "password": PW}).status_code == 200
    assert api.get("/api/report").json()["casts"] == []
    (rec / "exploit.cast").write_bytes(cast([0.5, "o", "x\r\n"]))
    assert api.get("/api/report").json()["casts"] == ["CC-0001"]


def test_report_markdown_download_is_an_attachment(api, tmp_path):
    assert api.get("/api/report.md").status_code == 401
    api.post("/api/login", json={"username": "ana", "password": PW})
    r = api.get("/api/report.md")
    assert r.status_code == 200 and r.content.startswith(b"# Report")
    assert r.headers["content-type"].startswith("text/markdown") and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"].startswith("attachment;") and 'report.md"' in r.headers["content-disposition"]
    (tmp_path / "ws" / "itemdb" / "reports" / "report.md").unlink()
    assert api.get("/api/report.md").status_code == 404
