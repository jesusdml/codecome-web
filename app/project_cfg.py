# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""The audit's name lives in CodeCome's own configuration, `codecome.yml` → `project.name`
(CodeCome prints it; the ledger and the archives are keyed by it).

We change ONLY that line, never re-dump the YAML: the rest of the file (comments, agents, scope)
is CodeCome's and stays byte for byte. The result is re-parsed to check the edit took.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml

NAME_RE = re.compile(r"^[\w][\w .\-]{0,79}$")  # letters (any language), digits, space, dot, dash, underscore
MAX_CFG = 64 * 1024


class ProjectNameError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def clean_name(name) -> str:
    n = name.strip() if isinstance(name, str) else ""
    if not NAME_RE.match(n) or n.endswith((" ", ".")):
        raise ProjectNameError("project_name_invalid")
    return n


def read_name(text: str) -> str | None:
    try:
        cfg = yaml.safe_load(text) or {}
        v = (cfg.get("project") or {}).get("name") if isinstance(cfg, dict) else None
        return str(v) if v is not None else None
    except (yaml.YAMLError, AttributeError):
        return None


def with_name(text: str, name: str) -> str:
    """`text` with project.name set to `name` (a JSON string is a valid YAML double-quoted scalar)."""
    value = json.dumps(name, ensure_ascii=False)
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if re.match(r"^project:\s*(#.*)?$", l)), None)
    if start is None:
        new = f"project:\n  name: {value}\n" + ("\n" if text.strip() else "") + text
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() and not lines[i][0].isspace()), len(lines))
        idx = next((i for i in range(start + 1, end) if re.match(r"^\s+name\s*:", lines[i])), None)
        if idx is not None:
            indent = re.match(r"^(\s+)", lines[idx]).group(1)
            nl = "\n" if lines[idx].endswith("\n") else ""
            lines[idx] = f"{indent}name: {value}{nl}"
        else:
            lines.insert(start + 1, f"  name: {value}\n")
        new = "".join(lines)
    if read_name(new) != name:
        raise ProjectNameError("project_name_write_failed")
    return new


def set_name(path: Path, name: str) -> bool:
    """Write project.name into codecome.yml (created if missing). Returns True if it changed."""
    path = Path(path)
    name = clean_name(name)
    old = ""
    if path.exists():
        if path.is_symlink() or path.stat().st_size > MAX_CFG:
            raise ProjectNameError("project_name_write_failed")
        old = path.read_text(encoding="utf-8")
        if read_name(old) == name:
            return False
    new = with_name(old, name)
    mode = (path.stat().st_mode & 0o777) if path.exists() else 0o664
    tmp = path.with_name("." + path.name + ".ccw-tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        os.fchmod(f.fileno(), mode)
        f.write(new)
    os.replace(tmp, path)
    return True
