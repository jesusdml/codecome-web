# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import Auth, hash_password
from app.itemdb import ItemDB

FIX = Path(__file__).parent / "fixtures"


PW = "una-contraseña-larga-123"


@pytest.fixture()
def anon(monkeypatch):
    monkeypatch.setattr(main, "db", ItemDB(FIX))
    monkeypatch.setattr(main, "auth", Auth({"ana": hash_password(PW)}, b"k" * 32))
    return TestClient(main.app)


@pytest.fixture()
def client(anon):
    r = anon.post("/api/login", json={"username": "ana", "password": PW})
    assert r.status_code == 200
    return anon


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


def test_list_and_counts(client):
    d = client.get("/api/findings").json()
    assert d["counts"]["PENDING"] >= 1 and d["counts"]["CONFIRMED"] == 1
    assert {f["id"] for f in d["findings"]} >= {"CC-0001", "CC-0002"}


def test_filter_by_status(client):
    d = client.get("/api/findings?status=CONFIRMED").json()
    assert [f["id"] for f in d["findings"]] == ["CC-0002"]


def test_bad_status_400(client):
    assert client.get("/api/findings?status=../etc").status_code == 400


def test_detail(client):
    d = client.get("/api/findings/CC-0001").json()
    assert d["severity"] == "HIGH" and "Summary" in d["body"]


def test_not_found_404(client):
    assert client.get("/api/findings/CC-9999").status_code == 404


@pytest.mark.parametrize("bad", ["..", "CC-1/../x", "CC-1;ls", "cc-0001", "CC-", "CC-12345678"])
def test_invalid_id_rejected(client, bad):
    assert client.get(f"/api/findings/{bad}").status_code in (400, 404)


def test_broken_file_does_not_break_list(client):
    assert client.get("/api/findings").status_code == 200


def test_index_served(client):
    assert "CodeCome" in client.get("/").text


def test_public_ca_certificate_is_served_but_never_a_key(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import main
    ca = tmp_path / "ca.crt"
    monkeypatch.setattr(main, "TLS_CA", ca)
    c = TestClient(main.app)
    assert c.get("/ca.crt").status_code == 404                           # not set up yet
    ca.write_text("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
    r = c.get("/ca.crt")                                                 # no login needed: it is public
    assert r.status_code == 200 and r.headers["content-type"] == "application/x-x509-ca-cert"
    assert "attachment" in r.headers["content-disposition"]
    ca.write_text("-----BEGIN CERTIFICATE-----\n-----BEGIN PRIVATE KEY-----\n")
    assert c.get("/ca.crt").status_code == 404                           # never serve anything with a key
