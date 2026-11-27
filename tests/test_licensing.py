# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Licensing stays complete: canonical GPL/AGPL texts, NOTICE, a license for every vendored file and
an SPDX header on every source file we write (new files included)."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPR = "GPL-3.0-or-later OR AGPL-3.0-or-later"
CANONICAL = {  # sha256 of the texts published by the FSF (gnu.org/licenses/gpl-3.0.txt, agpl-3.0.txt)
    "LICENSE": "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986",
    "AGPL-LICENSE": "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0",
}
SOURCES = ["app/**/*.py", "tests/**/*.py", "tools/**/*.py", "*.py", "app/static/js/**/*.js", "app/static/*.css",
           "app/static/*.html", "deploy/*.sh", "deploy/*.service", "tools/*.sh", "*.sh"]


def test_license_texts_are_the_canonical_ones():
    for name, digest in CANONICAL.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name


def test_notice_states_copyright_and_the_dual_license():
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "Copyright (C) 2026 @jesusdml" in notice and EXPR in notice


def test_every_vendored_file_has_its_license_and_is_in_the_notice():
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    vendor = ROOT / "app/static/vendor"
    for lic in ("LICENSE-htm.txt", "LICENSE-preact.txt", "fonts/OFL-Inter.txt", "fonts/OFL-JetBrainsMono.txt"):
        assert (vendor / lic).stat().st_size > 500, lic
        assert lic.split("/")[-1] in notice, lic
    shipped = [p.relative_to(vendor).as_posix() for p in vendor.rglob("*") if p.is_file()
               and p.suffix in (".js", ".woff2")]
    assert shipped, "vendor/ is empty?"
    for f in shipped:  # a new vendored file must be added to NOTICE (by name or family)
        stem = f.split("/")[-1].split("-latin")[0].split(".")[0]
        assert stem in notice or f in notice, f"{f} is not in NOTICE"


def test_every_source_file_has_an_spdx_header():
    missing = []
    for pat in SOURCES:
        for p in ROOT.glob(pat):
            if "vendor" in p.parts or ".venv" in p.parts:
                continue
            head = p.read_text(encoding="utf-8")[:400]
            if f"SPDX-License-Identifier: {EXPR}" not in head or "SPDX-FileCopyrightText: 2026 @jesusdml" not in head:
                missing.append(p.relative_to(ROOT).as_posix())
    assert not missing, missing
