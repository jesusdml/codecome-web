# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Read-only access to CodeCome's itemdb.

Findings are Markdown files with a YAML header, stored in
`<root>/itemdb/findings/<STATUS>/CC-XXXX*.md`. The real status is the folder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

STATUSES = ("PENDING", "CONFIRMED", "EXPLOITED", "REJECTED", "DUPLICATE")
ID_RE = re.compile(r"^CC-\d{1,6}$")
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


class FindingNotFound(Exception):
    pass


class InvalidFindingId(ValueError):
    pass


@dataclass(frozen=True)
class Finding:
    id: str
    status: str  # status given by the folder
    meta: dict
    body: str

    def summary(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "title": self.meta.get("title", ""),
            "severity": self.meta.get("severity", ""),
            "confidence": self.meta.get("confidence", ""),
            "category": self.meta.get("category", ""),
            "files": self.meta.get("files") or [],
            "updated_at": str(self.meta.get("updated_at", "")),
        }

    def detail(self) -> dict:
        return {**self.summary(), "meta": self.meta, "body": self.body}


def validate_id(finding_id: str) -> str:
    """The id comes from the user: only CC-<digits> is accepted."""
    if not ID_RE.fullmatch(finding_id):
        raise InvalidFindingId(finding_id)
    return finding_id


def _parse(path: Path, status: str) -> Finding:
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if m:
        meta = yaml.safe_load(m.group(1)) or {}
        body = m.group(2)
    else:
        meta, body = {}, text
    if not isinstance(meta, dict):
        meta = {}
    fid = str(meta.get("id") or path.stem.split("_")[0].split("-slug")[0])
    # The file name's id wins when the header is malformed.
    name_id = re.match(r"(CC-\d+)", path.name)
    if name_id:
        fid = name_id.group(1)
    return Finding(id=fid, status=status, meta=meta, body=body)


class ItemDB:
    def __init__(self, root: Path):
        self.findings_dir = Path(root).resolve() / "itemdb" / "findings"

    def _files(self):
        for status in STATUSES:
            d = self.findings_dir / status
            if d.is_dir():
                for p in sorted(d.glob("CC-*.md")):
                    if p.is_file() and not p.is_symlink():
                        yield status, p

    def list_findings(self, status: str | None = None) -> list[Finding]:
        out = []
        for st, p in self._files():
            if status and st != status:
                continue
            try:
                out.append(_parse(p, st))
            except (OSError, yaml.YAMLError, UnicodeDecodeError):
                continue  # one broken file does not break the list
        return out

    def get_finding(self, finding_id: str) -> Finding:
        validate_id(finding_id)
        for f in self.list_findings():
            if f.id == finding_id:
                return f
        raise FindingNotFound(finding_id)

    def counts(self) -> dict[str, int]:
        c = {s: 0 for s in STATUSES}
        for f in self.list_findings():
            c[f.status] += 1
        return c
