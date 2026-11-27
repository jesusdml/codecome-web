# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Documents written by the agent (report, later notes and findings), made safe to show.

Everything under itemdb/ is untrusted text: the agent wrote it after reading the audited code.
So the markdown is never turned into an HTML string. It is parsed into a small tree of allowed
elements, every leaf is plain text, raw HTML in the source stays literal text, and the browser
builds DOM nodes from the tree (no innerHTML). Files are opened without following symlinks.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

from .itemdb import validate_id

DOC_MAX = 2 * 1024 * 1024        # a report is tens of KB; refuse anything absurd
GIF_MAX = 20 * 1024 * 1024
CAST_MAX = 5 * 1024 * 1024
CAST_EVENTS_MAX = 20_000
# Terminal output reduced to what the web player understands: text plus \n \r \b and two markers.
ERASE_LINE, CLEAR = "\x01", "\x02"
_CSI = re.compile(r"\x1b\[([0-9;?]*)([@-~])")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
_OTHER_ESC = re.compile(r"\x1b[@-Z\\-_]?")
_CTRL = re.compile(r"[\x00-\x07\x0b\x0c\x0e-\x1f\x7f]")
NODES_MAX = 50_000
SAFE_HREF = re.compile(r"^(https?://|#|\./|\.\./|[A-Za-z0-9_\-./]+$)")

# markdown-it node type -> tag sent to the browser. Anything not listed is dropped (its text kept).
BLOCK = {"paragraph": "p", "bullet_list": "ul", "ordered_list": "ol", "list_item": "li",
         "blockquote": "blockquote", "table": "table", "thead": "thead", "tbody": "tbody",
         "tr": "tr", "th": "th", "td": "td", "strong": "strong", "em": "em", "s": "s"}

_md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable(["table", "strikethrough"])


class DocError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def read_safe(path: Path, limit: int) -> bytes:
    """Regular file only, no symlink at the last component, bounded size."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        raise DocError("doc_not_found")
    except OSError:
        raise DocError("doc_unreadable")  # includes ELOOP for a symlink
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise DocError("doc_unreadable")
        if st.st_size > limit:
            raise DocError("doc_too_large")
        chunks, left = [], limit + 1
        while left > 0:
            b = os.read(fd, min(65536, left))
            if not b:
                break
            chunks.append(b)
            left -= len(b)
        data = b"".join(chunks)
        if len(data) > limit:
            raise DocError("doc_too_large")
        return data
    finally:
        os.close(fd)


def _inside(root: Path, p: Path) -> bool:
    # Parent directories must not be symlinks leading out of the workspace either.
    try:
        return p.parent.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def md_tree(text: str) -> list:
    count = 0

    def conv(node: SyntaxTreeNode) -> list:
        nonlocal count
        count += 1
        if count > NODES_MAX:
            raise DocError("doc_too_large")
        t = node.type
        if t == "text":
            return [node.content]
        if t in ("code_inline",):
            return [{"t": "code", "c": [node.content]}]
        if t in ("fence", "code_block"):
            return [{"t": "pre", "c": [node.content]}]
        if t in ("softbreak",):
            return ["\n"]
        if t == "hardbreak":
            return [{"t": "br"}]
        if t == "hr":
            return [{"t": "hr"}]
        if t in ("html_block", "html_inline"):  # only reachable if html were enabled: keep literal
            return [node.content]
        if t == "image":  # images in agent documents are not loaded; keep the alt text
            return [kid for c in node.children for kid in conv(c)]
        kids = [kid for c in node.children for kid in conv(c)]
        if t == "heading":
            return [{"t": node.tag if node.tag in ("h1", "h2", "h3", "h4", "h5", "h6") else "h6", "c": kids}]
        if t == "link":
            href = str(node.attrs.get("href", ""))
            if SAFE_HREF.match(href) and not href.startswith("//"):  # "//host" would leave the site
                return [{"t": "a", "href": href, "c": kids}]
            return kids
        if t in ("th", "td"):
            align = str(node.attrs.get("style", "")).removeprefix("text-align:")
            n = {"t": BLOCK[t], "c": kids}
            if align in ("left", "center", "right"):
                n["align"] = align
            return [n]
        if t in BLOCK:
            return [{"t": BLOCK[t], "c": kids}]
        return kids  # root, inline and anything unknown: flatten

    return conv(SyntaxTreeNode(_md.parse(text)))


def _clean_term(data: str) -> str:
    """Agent-recorded terminal bytes -> plain text the player can replay. Unknown escapes are dropped."""
    def csi(m):
        args, final = m.group(1), m.group(2)
        if final == "K":
            return ERASE_LINE
        if final == "J" and args in ("2", "3"):
            return CLEAR
        if final == "H" and args in ("", "1;1"):
            return ""  # home: only meaningful together with a clear
        return ""
    data = _OSC.sub("", data)
    data = _CSI.sub(csi, data)
    data = _OTHER_ESC.sub("", data)
    # Keep our two markers, newline, carriage return and backspace; drop every other control char.
    return "".join(c for c in data if c in (ERASE_LINE, CLEAR, "\n", "\r", "\b", "\t") or not _CTRL.match(c))


def parse_cast(raw: bytes) -> dict:
    """asciicast v2: header line + one JSON array per event. Bad lines are skipped, not fatal."""
    lines = raw.decode("utf-8", "replace").splitlines()
    try:
        head = json.loads(lines[0]) if lines else {}
    except ValueError:
        raise DocError("doc_unreadable")
    if not isinstance(head, dict) or head.get("version") != 2:
        raise DocError("doc_unreadable")
    limit = head.get("idle_time_limit")
    limit = float(limit) if isinstance(limit, (int, float)) and limit > 0 else None
    events, last_raw, clock = [], 0.0, 0.0
    for line in lines[1:]:
        if len(events) >= CAST_EVENTS_MAX:
            break
        try:
            t, kind, data = json.loads(line)
            t = float(t)
        except (ValueError, TypeError):
            continue
        if kind != "o" or not isinstance(data, str):
            continue  # input echoes, markers and resizes are not shown
        gap = max(0.0, t - last_raw)
        last_raw = t
        clock += min(gap, limit) if limit else gap  # same idle compression asciinema applies
        events.append([round(clock, 3), _clean_term(data)])
    def num(v, d):
        return int(v) if isinstance(v, int) and 10 <= v <= 400 else d
    title = head.get("title")
    return {"width": num(head.get("width"), 100), "height": num(head.get("height"), 30),
            "title": title[:200] if isinstance(title, str) else "",
            "duration": events[-1][0] if events else 0, "events": events}  # one event = one step


class Docs:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def report(self) -> dict:
        p = self.root / "itemdb" / "reports" / "report.md"
        if not _inside(self.root, p):
            raise DocError("doc_unreadable")
        raw = read_safe(p, DOC_MAX)
        st = p.lstat()
        return {"path": "itemdb/reports/report.md", "size": len(raw), "mtime": st.st_mtime,
                "tree": md_tree(raw.decode("utf-8", "replace")), "recordings": self.recordings(),
                "casts": self.casts()}

    def report_raw(self) -> bytes:
        p = self.root / "itemdb" / "reports" / "report.md"
        if not _inside(self.root, p):
            raise DocError("doc_unreadable")
        return read_safe(p, DOC_MAX)

    def _gif_path(self, finding: str) -> Path:
        return self.root / "itemdb" / "evidence" / validate_id(finding) / "exploits" / "recordings" / "exploit.gif"

    def recordings(self) -> list[str]:
        ev = self.root / "itemdb" / "evidence"
        out = []
        if ev.is_dir():
            for d in sorted(ev.iterdir()):
                try:
                    p = self._gif_path(d.name)
                except ValueError:
                    continue
                if p.is_file() and not p.is_symlink() and _inside(self.root, p):
                    out.append(d.name)
        return out

    def recording_cast(self, finding: str) -> dict:
        p = self.root / "itemdb" / "evidence" / validate_id(finding) / "exploits" / "recordings" / "exploit.cast"
        if not _inside(self.root, p):
            raise DocError("doc_unreadable")
        return parse_cast(read_safe(p, CAST_MAX))

    def casts(self) -> list[str]:
        ev = self.root / "itemdb" / "evidence"
        out = []
        if ev.is_dir():
            for d in sorted(ev.iterdir()):
                try:
                    if self.has_cast(d.name):
                        out.append(d.name)
                except ValueError:
                    continue
        return out

    def has_cast(self, finding: str) -> bool:
        p = self.root / "itemdb" / "evidence" / validate_id(finding) / "exploits" / "recordings" / "exploit.cast"
        return p.is_file() and not p.is_symlink() and _inside(self.root, p)

    def recording_gif(self, finding: str) -> bytes:
        p = self._gif_path(finding)
        if not _inside(self.root, p):
            raise DocError("doc_unreadable")
        data = read_safe(p, GIF_MAX)
        if not data.startswith((b"GIF87a", b"GIF89a")):
            raise DocError("doc_not_gif")  # served as image/gif only if it really is one
        return data
