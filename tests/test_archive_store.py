# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""v1.1 step 5: the Archive screen reads only the checked fields of the worker's cards."""
import json
import os

import pytest
from fastapi.testclient import TestClient

from app import archive_store, main
from app.auth import Auth, hash_password
from tests.test_api import PW

AID = "demo-app-20261127-101500"


def card(store, aid=AID, **extra):
    data = {"format": 1, "id": aid, "project": "Demo App", "created_at": 1764238500.0, "by": "ana", "size": 2048,
            "sha256": "a" * 64, "files": 8, "bytes": 9000, "jobs": 3, "assumed": 1, "cost_usd": 1.23456,
            "phases": {"1": "done", "2": "done", "3": "done", "6": "none", "9": "done", "1x": "<b>"},
            "findings": {"PENDING": 2, "EVIL": 5, "CONFIRMED": -1}, "secret": "never shown"}
    data.update(extra)
    (store / f"{aid}.json").write_text(json.dumps(data))
    (store / f"{aid}.tar.gz.gpg").write_bytes(b"\x8c\x0d" + b"x" * 100)


def test_cards_keep_only_checked_fields(tmp_path):
    card(tmp_path)
    card(tmp_path, "older-20261101-090000", created_at=1761987600.0, project="<script>", cost_usd="free")
    a, b = archive_store.list_archives(tmp_path)
    assert a["id"] == AID and b["id"] == "older-20261101-090000"
    assert a["phases"] == {"1": "done", "2": "done", "3": "done", "6": "none"}
    assert a["findings"] == {"PENDING": 2} and a["cost_usd"] == 1.2346 and "secret" not in a
    assert b["project"] == "<script>" and b["cost_usd"] is None        # text stays text; bad types dropped


def test_broken_or_orphan_cards_are_skipped(tmp_path):
    card(tmp_path)
    (tmp_path / "x-20261127-000000.json").write_text("{not json")
    (tmp_path / "orphan-20261127-000001.json").write_text(json.dumps({"id": "orphan-20261127-000001"}))
    (tmp_path / "Bad Name.json").write_text("{}")
    card(tmp_path, "wrongid-20261127-000002", id="other-20261127-000002")
    os.symlink(tmp_path / f"{AID}.json", tmp_path / "link-20261127-000003.json")
    (tmp_path / "link-20261127-000003.tar.gz.gpg").write_bytes(b"x")
    (tmp_path / "huge-20261127-000004.json").write_text(json.dumps({"id": "huge-20261127-000004", "pad": "x" * 70000}))
    (tmp_path / "huge-20261127-000004.tar.gz.gpg").write_bytes(b"x")
    assert [c["id"] for c in archive_store.list_archives(tmp_path)] == [AID]
    assert archive_store.list_archives(tmp_path / "missing") == []


@pytest.fixture()
def c(monkeypatch, tmp_path):
    card(tmp_path)
    monkeypatch.setattr(main, "ARCHIVES_DIR", tmp_path)
    monkeypatch.setattr(main, "auth", Auth({"ana": hash_password(PW), "bob": hash_password(PW)}, b"k" * 32))
    monkeypatch.setattr(main, "ADMINS", {"ana"})

    def login(u):
        cl = TestClient(main.app)
        assert cl.post("/api/login", json={"username": u, "password": PW}).status_code == 200
        return cl
    return login


def test_everyone_sees_the_list_only_admins_download(c):
    bob = c("bob").get("/api/archives").json()
    assert bob["can_download"] is False and bob["archives"][0]["id"] == AID
    assert c("bob").get(f"/api/archives/{AID}/file").status_code == 403
    r = c("ana").get(f"/api/archives/{AID}/file")
    assert r.status_code == 200 and r.content.startswith(b"\x8c\x0d")
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["x-content-type-options"] == "nosniff"
    assert f'{AID}.tar.gz.gpg' in r.headers["content-disposition"] and "attachment" in r.headers["content-disposition"]


@pytest.mark.parametrize("aid", ["..%2F..%2Fetc%2Fpasswd", "missing-20261127-000000", "DEMO-20261127-101500", "x"])
def test_download_only_known_ids(c, aid):
    assert c("ana").get(f"/api/archives/{aid}/file").status_code == 404
