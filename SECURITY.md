# Security policy

CodeCome Web is a tool for security work, so reports about its own security are very welcome.

## Reporting a vulnerability

**Please do not open a public issue.** Report it privately through GitHub:

1. Go to the repository's **Security** tab.
2. Click **Report a vulnerability** (GitHub's private vulnerability reporting).
3. Describe the problem. A good report includes:
   - the affected version (`version` in `pyproject.toml`, or the release tag),
   - what an attacker can do and from where (a logged-in auditor? anyone on the network? the
     code under audit? the output of an AI agent?),
   - steps to reproduce, or a proof of concept,
   - anything you know about a fix.

Only you and the maintainer see the report until it is published.

## What to expect

This is a personal project maintained in spare time, so there is no formal SLA. In practice:

- an acknowledgement within a few days;
- a first assessment (valid or not, severity) within about two weeks;
- a fix in a new release as soon as reasonably possible, with credit in the release notes if you
  want it.

Please give a reasonable time to ship a fix before disclosing details publicly.

## Supported versions

Only the **latest release** (currently the 1.1.x line) receives security fixes. Upgrading is a
re-run of `install.sh`.

## Scope

In scope: this repository — the web service, the worker, the installer and uninstaller, the TLS
setup, the release tooling, and how they handle untrusted input (uploaded archives, encrypted
archives, and everything the AI agents write: findings, notes, reports, recordings).

Out of scope:

- vulnerabilities in **CodeCome** itself: please report them to the
  [CodeCome project](https://github.com/pruiz/CodeCome);
- vulnerabilities in third-party components (FastAPI, Uvicorn, Preact, htm, GnuPG, OpenSSL…):
  report them upstream (a note here is still welcome if CodeCome Web uses them in an unsafe way);
- findings that need an already-compromised host or an administrator acting against their own
  installation;
- the invented data in the screenshots and the demo video.

## Design notes for researchers

The [README's security model](README.md#security-model) describes the boundaries this project
intends to keep: the web never executes anything, agent output is always rendered as text,
uploads go through a hardened extractor, archive passwords are never stored, and both services
run hardened under systemd. A way around any of those is exactly the kind of report we want.
