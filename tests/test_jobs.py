# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
import os
import time

import pytest

from app.itemdb import ItemDB
from app.jobs import DuplicateJob, InvalidJob, JobDB, build_argv
from tests.test_api import FIX


@pytest.fixture()
def db(tmp_path):
    return JobDB(tmp_path / "jobs.db")


IT = ItemDB(FIX)


def test_build_argv_is_a_list_without_shell():
    assert build_argv("phase-1", None) == ["make", "phase-1"]
    assert build_argv("phase-4", "CC-0001") == ["make", "phase-4", "FINDING=CC-0001"]


@pytest.mark.parametrize("action,finding", [
    ("phase-9", None), ("rm -rf /", None), ("phase-1; ls", None),
    ("phase-4", None), ("phase-4", "CC-1;ls"), ("phase-4", "$(id)"), ("phase-4", "../x"),
    ("phase-1", "CC-0001"),
])
def test_build_argv_rejects_bad_input(action, finding):
    with pytest.raises(InvalidJob):
        try:
            build_argv(action, finding)
        except ValueError as e:  # InvalidFindingId es ValueError
            raise InvalidJob(str(e))


def test_enqueue_and_state_rules(db):
    assert db.enqueue("phase-1", None, "ana", IT) == 1
    assert db.enqueue("phase-4", "CC-0001", "ana", IT)  # PENDING -> ok
    with pytest.raises(InvalidJob):
        db.enqueue("phase-5", "CC-0001", "ana", IT)  # exige CONFIRMED
    assert db.enqueue("phase-5", "CC-0002", "ana", IT)
    with pytest.raises(InvalidJob):
        db.enqueue("phase-4", "CC-9999", "ana", IT)


def test_duplicates_rejected_but_allowed_after_finish(db, tmp_path):
    db.enqueue("phase-1", None, "ana", IT)
    with pytest.raises(DuplicateJob):
        db.enqueue("phase-1", None, "ana", IT)
    job = db.claim_next(tmp_path)
    db.finish(job["id"], "done", 0)
    db.enqueue("phase-1", None, "ana", IT)


def test_claim_is_fifo_and_exclusive(db, tmp_path):
    a = db.enqueue("phase-1", None, "ana", IT)
    b = db.enqueue("phase-2", None, "ana", IT)
    j1 = db.claim_next(tmp_path)
    assert j1["id"] == a and j1["status"] == "running"
    assert db.claim_next(tmp_path)["id"] == b
    assert db.claim_next(tmp_path) is None


def test_cancel_queued_and_running(db, tmp_path):
    a = db.enqueue("phase-1", None, "ana", IT)
    assert db.request_cancel(a) and db.get(a)["status"] == "cancelled"
    b = db.enqueue("phase-2", None, "ana", IT)
    db.claim_next(tmp_path)
    assert db.request_cancel(b) and db.cancel_requested(b)
    assert not db.request_cancel(999)


def test_mark_interrupted(db, tmp_path):
    a = db.enqueue("phase-1", None, "ana", IT)
    db.claim_next(tmp_path)
    assert db.mark_interrupted() == 1
    assert db.get(a)["status"] == "interrupted"


def test_worker_heartbeat(db):
    assert db.worker_state()["alive"] is False
    db.beat(123)
    assert db.worker_state()["alive"] is True
    assert db.worker_state(now=time.time() + 60)["alive"] is False


def test_db_files_are_group_writable_for_two_users(tmp_path):
    """web and worker are different users of the same group: nothing may stay 0644."""
    import os
    import stat
    old = os.umask(0o002)
    try:
        d = JobDB(tmp_path / "jobs.db")
        d.enqueue("phase-1", None, "ana", IT)
        with d._conn() as c:  # with an open connection, -wal and -shm exist
            c.execute("INSERT INTO worker(id,pid,beat) VALUES(1,1,1) ON CONFLICT(id) DO NOTHING")
            names = sorted(p.name for p in tmp_path.iterdir())
            assert {"jobs.db", "jobs.db-wal", "jobs.db-shm"} <= set(names)
            for n in names:
                mode = stat.S_IMODE((tmp_path / n).stat().st_mode)
                assert mode & 0o060 == 0o060 and mode & 0o007 == 0, (n, oct(mode))
    finally:
        os.umask(old)


def test_existing_db_owned_by_someone_else_does_not_crash(tmp_path, monkeypatch):
    p = tmp_path / "jobs.db"
    JobDB(p)
    monkeypatch.setattr(os, "chmod", lambda *a, **k: (_ for _ in ()).throw(PermissionError()))
    JobDB(p)  # the second user cannot chmod and must not fail
