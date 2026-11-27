#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Makes a PUBLIC release: one snapshot commit on the orphan branch `public`, built from a commit of
the private history (default: main). It never merges: the private history has deployment details
that must not leave this repository.

    .venv/bin/python tools/release.py [--ref main] [--dry-run]

1. `git archive` of the ALLOWLIST only (everything else stays private: docs/, deploy/apply.sh,
   this host's units, config/profiles.json...);
2. leak scan of every exported file (private addresses and names, e-mails, secret-looking strings);
3. the test suite, run INSIDE the export (proves it works without the private files);
4. commit on `public` ("Release vX.Y.Z (from <ref> <sha>)") and tag `vX.Y.Z` on it.
Nothing is pushed: pushing is a separate, explicit step.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRANCH = "public"

ALLOW = [  # paths (files or directories) that are published
    "app", "tests", "tools", "config/profiles.example.json", "config/prices.json", "config/mock",
    "deploy/tls-setup.sh", "deploy/templates", "install.sh", "uninstall.sh", "worker.py", "run.sh",
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", ".env.example", ".gitignore",
    "README.md", "ROADMAP.md", "SECURITY.md", "LICENSE", "AGPL-LICENSE", "NOTICE", ".github", "media",
]

# Private markers: generic checks + this installation's names from private-markers.txt (NOT published).
sys.path.insert(0, str(ROOT))
from tests.test_public_clean import load_markers  # noqa: E402

PRIVATE = load_markers(ROOT)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_OK = re.compile(r"noreply@anthropic\.com|@example\.(com|org)|@users\.noreply\.github\.com|@fsf\.org"
                      r"|@gnu\.org|@[a-z.]*\.test\b|@localhost\b|license@|licensing@")
SECRET = re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}"
                    r"|-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]{40,}?-----END")
# Known, intentional matches (test data that must contain such a string): file -> pattern
ALLOWED_HITS = {
    "tests/test_api.py": "BEGIN PRIVATE KEY",         # proves /ca.crt never serves a key (no key body)
}


class ReleaseError(Exception):
    pass


def git(*args: str, cwd: Path = ROOT, capture: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=capture, text=True)
    if r.returncode:
        raise ReleaseError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip() if capture else ""


def export(ref: str, dest: Path) -> list[str]:
    tracked = set(git("ls-tree", "-r", "--name-only", ref).splitlines())
    paths = [p for p in ALLOW if any(t == p or t.startswith(p + "/") for t in tracked)]
    data = subprocess.run(["git", "archive", "--format=tar", ref, "--", *paths], cwd=ROOT,
                          capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(dest, filter="data")
    return sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())


def scan(dest: Path, files: list[str]) -> list[str]:
    hits = []
    for rel in files:
        raw = (dest / rel).read_bytes()
        if b"\0" in raw[:4096]:
            continue  # binary (fonts): nothing readable to leak
        text = raw.decode("utf-8", "replace")
        allowed = ALLOWED_HITS.get(rel)
        for i, line in enumerate(text.splitlines(), 1):
            clean = line.replace("@jesusdml", "")
            if PRIVATE.search(clean):
                hits.append(f"{rel}:{i}: private name/address: {line.strip()[:90]}")
            for e in EMAIL.findall(line):
                if not EMAIL_OK.search(e) and not e.startswith(("noreply@", "user@")):
                    hits.append(f"{rel}:{i}: e-mail {e}")
        for m in SECRET.finditer(text):
            if not (allowed and allowed in m.group(0)):
                hits.append(f"{rel}: secret-looking string {m.group(0)[:30]}…")
    return hits


def run_tests(dest: Path) -> None:
    py = ROOT / ".venv/bin/python"
    env = {k: v for k, v in os.environ.items() if not k.startswith("CODECOME_")}
    r = subprocess.run([str(py), "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], cwd=dest, env=env,
                       capture_output=True, text=True)
    tail = "\n".join(r.stdout.strip().splitlines()[-3:])
    print("   " + tail.replace("\n", "\n   "))
    if r.returncode:
        raise ReleaseError("tests failed inside the export")


def commit_public(dest: Path, version: str, ref: str, sha: str) -> str:
    tag = f"v{version}"
    if git("tag", "-l", tag):
        raise ReleaseError(f"tag {tag} already exists: bump the version in pyproject.toml first")
    work = Path(tempfile.mkdtemp(prefix="ccw-public-"))
    try:
        exists = bool(git("branch", "--list", BRANCH))
        git("worktree", "add", "--detach", str(work), capture=False)
        if exists:
            git("checkout", "-q", BRANCH, cwd=work)
        else:
            git("checkout", "-q", "--orphan", BRANCH, cwd=work)
        for p in work.iterdir():  # replace the whole tree with the export
            if p.name != ".git":
                shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
        shutil.copytree(dest, work, dirs_exist_ok=True, symlinks=True)
        git("add", "-A", cwd=work)
        msg = f"Release {tag}\n\nSnapshot of {ref} {sha[:12]} (allowlisted, leak-checked, tests passed)."
        git("commit", "-q", "-m", msg, cwd=work)
        new = git("rev-parse", "HEAD", cwd=work)
        git("tag", "-a", tag, "-m", f"CodeCome Web {tag}", new)
        return new
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(work)], cwd=ROOT, capture_output=True)
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ref", default="main", help="private commit/branch/tag to publish (default main)")
    ap.add_argument("--dry-run", action="store_true", help="export, scan and test; do not commit")
    a = ap.parse_args()
    try:
        if git("status", "--porcelain"):
            raise ReleaseError("the working tree has uncommitted changes")
        sha = git("rev-parse", "--verify", f"{a.ref}^{{commit}}")
        version = tomllib.loads(git("show", f"{sha}:pyproject.toml"))["project"]["version"]
        print(f"== release v{version} from {a.ref} {sha[:12]}")
        with tempfile.TemporaryDirectory(prefix="ccw-release-") as tmp:
            dest = Path(tmp)
            files = export(sha, dest)
            print(f"== exported {len(files)} allowlisted files")
            hits = scan(dest, files)
            if hits:
                print("== LEAK CHECK FAILED:\n   " + "\n   ".join(hits[:50]))
                return 1
            print("== leak check: clean")
            print("== tests inside the export")
            run_tests(dest)
            if a.dry_run:
                print("== dry run: nothing committed")
                return 0
            new = commit_public(dest, version, a.ref, sha)
        print(f"== committed {new[:12]} on '{BRANCH}' and tagged v{version}. Nothing pushed.")
        print(f"   review: git log --stat {BRANCH} -1    push (when ready): git push <remote> {BRANCH}:main v{version}")
        return 0
    except ReleaseError as e:
        print(f"xx {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
