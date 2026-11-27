# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""What goes to the public branch carries no private data from the development network and no
Spanish left in the code (user-facing text lives in app/locales/). deploy/ and config/profiles.json
are this installation's own files and are NOT published (the installer generates them)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_CODE = ["app/**/*.py", "app/static/js/**/*.js", "app/static/*.css", "app/static/*.html", "tests/**/*.py",
               "tools/**/*.py", "tools/*.sh", "*.py", "*.sh", "config/*.example.json", "config/prices.json",
               ".env.example", "pyproject.toml", "NOTICE", "README.md", "ROADMAP.md", "SECURITY.md"]
GENERIC = [r"192\.168\.\d+\.\d+", r"/home/(?!(?:x|dev)\b)\w+"]  # private LAN addresses, real home folders


def load_markers(root: Path = ROOT) -> re.Pattern:
    """Generic checks + this installation's own names from private-markers.txt (kept out of the
    public branch: publishing the list would itself leak the names)."""
    extra = []
    f = root / "private-markers.txt"
    if f.exists():
        extra = [l.strip() for l in f.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    return re.compile("|".join(GENERIC + extra), re.I)


PRIVATE = load_markers()
# Spanish words that do not exist in English: a comment with two of them was not translated.
SPANISH = re.compile(r"\b(fichero|trabajo|hallazgo|usuario|contraseña|nunca|siempre|también|porque|cuando|aunque|todavía|"
                     r"después|antes de|para que|sin embargo|guion|lanzar|devuelve|comprueba|uso|nombre|repite|"
                     r"coinciden|mínimo|caracteres|guardado|ejecútalo)\b", re.I)
INTENTIONAL = ("tests/test_i18n.py", "tests/test_api.py", "tests/test_upload.py", "tests/test_archive.py",
               "tests/test_jobs_api.py", "tests/test_public_clean.py")  # test data in Spanish on purpose


def files():
    for pat in PUBLIC_CODE:
        for p in ROOT.glob(pat):
            if "vendor" not in p.parts and ".venv" not in p.parts:
                yield p, p.relative_to(ROOT).as_posix()


def test_no_private_names_or_addresses():
    hits = []
    for p, rel in files():
        if rel == "tests/test_public_clean.py":
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            line = line.replace("SPDX-FileCopyrightText: 2026 @jesusdml", "").replace("Copyright (C) 2026 @jesusdml", "")
            if PRIVATE.search(line):
                hits.append(f"{rel}:{i}: {line.strip()[:100]}")
    assert not hits, hits


def test_no_spanish_left_in_the_code():
    hits = []
    for p, rel in files():
        if rel in INTENTIONAL:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if SPANISH.search(line) and (re.search(r"[ñáéíóú]|\b(el|la|los|las|que|del|por|no)\b", line)
                                         or re.search(r"\b(Uso|Repite|NOMBRE)\b", line)):
                hits.append(f"{rel}:{i}: {line.strip()[:100]}")
    assert not hits, hits
