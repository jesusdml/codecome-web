#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Worker: runs the queued jobs. It is the only process with access to Docker.

Variables: CODECOME_ROOT (CodeCome's repository), CODECOME_DATA (jobs.db and logs).
"""
import os
import signal
from pathlib import Path

from app import oneshot, uploads
from app.jobs import JobDB
from app.maintenance import make_handlers
from app.ledger import Ledger
from app.profiles import Profiles
from app.settings import Settings
from app.runner import Runner, work_loop
from app.safe_extract import cleanup_staging

ROOT = Path(os.environ.get("CODECOME_ROOT", "/opt/CodeCome"))
DATA = Path(os.environ.get("CODECOME_DATA", Path(__file__).resolve().parent / "data"))

if __name__ == "__main__":
    stop = False

    def _term(*_):
        global stop
        stop = True

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    db = JobDB(DATA / "jobs.db")
    profiles = Profiles(Path(os.environ.get("CODECOME_PROFILES", Path(__file__).resolve().parent / "config" / "profiles.json")),
                        Settings(DATA / "settings.json"))  # what the Settings page chose
    ledger = Ledger(DATA / "ledger.db")
    ledger.load_prices(Path(os.environ.get("CODECOME_PRICES", Path(__file__).resolve().parent / "config" / "prices.json")))
    # Leftovers of a crash: secrets of jobs that never ran, half-extracted staging dirs.
    oneshot.sweep(DATA / "secrets")
    cleanup_staging(ROOT)
    uploads.clear(DATA / "uploads", max_age=uploads.MAX_AGE_S)
    store = Path(os.environ.get("CODECOME_ARCHIVES", "/opt/codecome-archives"))
    cost = lambda project, since: ledger.usage(project=project, since=since)["total"]["cost"]  # noqa: E731
    maintenance = make_handlers(ROOT, DATA, store=store, db=db, cost=cost)  # upload, archive, rescue
    work_loop(db, Runner(db, ROOT, profiles=profiles, ledger=ledger, maintenance=maintenance,
                         secrets_dir=DATA / "secrets"), DATA / "logs", lambda: stop)
