# Roadmap

What is planned, roughly in order. Versions are targets, not promises; the Journey stays the
**only launch point** for everything below (plans and scheduling included).

## Done

- **v1.0** — Journey with live progress, findings, report (PDF and Markdown), recordings player,
  costs dashboard, "How it works", Settings (profiles and models per phase, admins only), "Assume"
  with a reason, seven languages, light/dark themes.
- **v1.1** — Encrypted archive (GnuPG AES-256) with verification, Archive screen, rescue, code upload
  through a hardened extractor with the new audit's name, empty without archiving, per-audit
  counters, HTTPS with a name-constrained local CA, one-command installer and uninstaller.

## v1.2 — Administration

- **Admin view** for what today needs a shell:
  - upload the definitive TLS certificate (cert + chain + key): checked (key matches, covers the
    host, not expired) before swapping it in, never locking the admin out;
  - API keys, **write-only**: sent to the worker through a one-shot secret and stored in its own
    0600 file; the web only shows "set, ends in …, date" and can never read a key back;
  - web users and administrators, model prices;
  - a read-only "System" panel with what the installer decided (paths, users, port).
- **Delete archived audits** from the Archive screen (admin only, typed confirmation, cannot be undone).
- **Totals across all audits** for administrators: cost, tokens, cache savings and machine time of
  the current, archived and discarded audits, per project and per month.

## v1.3 — Plans and code from git

- **Code from a git repository** as an alternative to uploading an archive: the admin gives the URL
  and a branch, tag or commit, plus the new audit's name; the worker clones it into an empty `src/`
  (shallow; no submodules, hooks or LFS fetched implicitly; size caps like uploads). Private
  repositories with a read-only deploy key or token, stored write-only like the API keys. The commit
  that was audited is recorded with the audit and shown in the report.
- **One engine for "launch 1 → 3" and "automate 4 · 5":** an ordered list of steps the worker takes
  one by one, each checked (gates, launch reasons) when it is about to run; a failure or a model
  refusal stops the plan and says so; one-click stop; live spend and an estimate before starting.
- **Select / skip per finding** for phases 4 and 5, so fifty findings do not mean fifty clicks.
- **"Next run"** next to "Last run" on each phase, showing the model it would use.
- **Refusals:** detect when a model refuses a phase and offer to retry it on another profile (for
  example a local model reserved for validation and exploitation).
- Smaller: "done" chips in the matrix open their job; going back from a job returns to the Journey;
  the Jobs tab becomes a debug view.

## v2 — Projects

- Several audits side by side, each in its own CodeCome workspace, with a history: name, date,
  phase, findings and cost per project, and a project selector on the home page.
- A safe inventory of uploaded code (what would be built or executed, secrets, text aimed at the
  agent) shown before phase 1 can run.
- Parallel runs with several workers and caps (sandbox resources, API rate limits, spend).

## Later

- Other distributions (dnf) and a Docker Compose install.
- Section 4 of "How it works" (the web's own architecture and security boundaries).
