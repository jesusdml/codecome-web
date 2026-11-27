# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""pyproject, the pinned requirements and the example configuration stay in step with the code."""
import re
import tomllib
from pathlib import Path

from app.profiles import Profiles
from tests.test_public_clean import PRIVATE

ROOT = Path(__file__).resolve().parent.parent
ENV_RE = re.compile(r"""environ(?:\.get)?\(\s*["']([A-Z][A-Z0-9_]+)["']|environ\[["']([A-Z][A-Z0-9_]+)["']\]""")


def test_every_variable_the_code_reads_is_documented():
    used = set()
    for p in [*ROOT.glob("app/**/*.py"), ROOT / "worker.py"]:
        for a, b in ENV_RE.findall(p.read_text(encoding="utf-8")):
            used.add(a or b)
    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", (ROOT / ".env.example").read_text(), re.M))
    assert used and not used - documented, sorted(used - documented)


def test_pyproject_matches_the_pins():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    names = lambda reqs: {re.split(r"[<>=!~ ]", r, 1)[0].lower() for r in reqs}
    pinned = [l for l in (ROOT / "requirements.txt").read_text().splitlines() if l and not l.startswith("#")]
    assert names(meta["dependencies"]) == names(pinned)
    assert meta["license"] == "GPL-3.0-or-later OR AGPL-3.0-or-later"
    for f in meta["license-files"]:
        assert list(ROOT.glob(f)), f


def test_example_profiles_load_and_are_clean(tmp_path):
    text = (ROOT / "config/profiles.example.json").read_text()
    assert not PRIVATE.search(text)
    names = {p["name"] for p in Profiles(ROOT / "config/profiles.example.json").public()["profiles"]}
    assert {"mock", "api", "local"} <= names


def test_readme_links_and_commands_exist():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for target in re.findall(r"\]\(([^)#:]+)\)", text):            # relative links
        assert (ROOT / target).exists(), target
    codecome_own = {"tools/gate-check.py",                          # CodeCome's files, not ours
                    "config/profiles.json"}                         # created by install.sh from the example
    for path in re.findall(r"\b(tools/[\w.-]+|deploy/[\w.-]+|config/[\w.-]+|app/[\w./*-]+)", text):
        assert path in codecome_own or list(ROOT.glob(path)), path
    for opt in re.findall(r"`(--[a-z-]+)`", text):                 # options the README promises
        assert opt in (ROOT / "install.sh").read_text() + (ROOT / "uninstall.sh").read_text() \
            + (ROOT / "deploy/tls-setup.sh").read_text(), opt


def test_release_allowlist_keeps_private_files_out():
    import tools.release as release
    allow = set(release.ALLOW)
    for private in ("docs", "deploy", "deploy/apply.sh", "config", "config/profiles.json", "private-markers.txt", "data", "demo"):
        assert private not in allow, private
    assert {"install.sh", "uninstall.sh", "LICENSE", "AGPL-LICENSE", "NOTICE", "README.md", ".github"} <= allow
    assert all((ROOT / p).exists() for p in allow), [p for p in allow if not (ROOT / p).exists()]
