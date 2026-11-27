# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import COOKIE, MAX_FAILS, Auth, hash_password
from tests.test_api import FIX, PW  # noqa: F401
from app.itemdb import ItemDB


def make(monkeypatch, now=None):
    a = Auth({"ana": hash_password(PW)}, b"k" * 32, **({"now": now} if now else {}))
    monkeypatch.setattr(main, "db", ItemDB(FIX))
    monkeypatch.setattr(main, "auth", a)
    return TestClient(main.app), a


@pytest.mark.parametrize("path", ["/api/findings", "/api/findings/CC-0001", "/api/me"])
def test_requires_login(monkeypatch, path):
    c, _ = make(monkeypatch)
    assert c.get(path).status_code == 401


def test_health_and_index_public(monkeypatch):
    c, _ = make(monkeypatch)
    assert c.get("/api/health").status_code == 200
    assert c.get("/").status_code == 200


def test_login_ok_sets_secure_cookie_flags(monkeypatch):
    c, _ = make(monkeypatch)
    r = c.post("/api/login", json={"username": "ana", "password": PW})
    h = r.headers["set-cookie"].lower()
    assert "httponly" in h and "samesite=strict" in h
    assert c.get("/api/me").json() == {"user": "ana"}


def test_wrong_password_and_unknown_user(monkeypatch):
    c, _ = make(monkeypatch)
    assert c.post("/api/login", json={"username": "ana", "password": "mala"}).status_code == 401
    assert c.post("/api/login", json={"username": "nadie", "password": PW}).status_code == 401


def test_lockout_after_failures(monkeypatch):
    c, _ = make(monkeypatch)
    for _ in range(MAX_FAILS):
        c.post("/api/login", json={"username": "ana", "password": "mala"})
    r = c.post("/api/login", json={"username": "ana", "password": PW})
    assert r.status_code == 429


def test_lockout_expires(monkeypatch):
    t = [1000.0]
    c, _ = make(monkeypatch, now=lambda: t[0])
    for _ in range(MAX_FAILS):
        c.post("/api/login", json={"username": "ana", "password": "mala"})
    t[0] += 301
    assert c.post("/api/login", json={"username": "ana", "password": PW}).status_code == 200


def test_tampered_cookie_rejected(monkeypatch):
    c, a = make(monkeypatch)
    tok = a.make_token("ana")
    c.cookies.set(COOKIE, tok[:-1] + ("0" if tok[-1] != "0" else "1"))
    assert c.get("/api/me").status_code == 401


def test_cookie_signed_with_other_key_rejected(monkeypatch):
    c, _ = make(monkeypatch)
    other = Auth({"ana": "x"}, b"z" * 32)
    c.cookies.set(COOKIE, other.make_token("ana"))
    assert c.get("/api/me").status_code == 401


def test_expired_session(monkeypatch):
    t = [1000.0]
    c, a = make(monkeypatch, now=lambda: t[0])
    c.post("/api/login", json={"username": "ana", "password": PW})
    t[0] += 9 * 3600
    assert c.get("/api/me").status_code == 401


def test_logout_clears_session(monkeypatch):
    c, _ = make(monkeypatch)
    c.post("/api/login", json={"username": "ana", "password": PW})
    c.post("/api/logout")
    assert c.get("/api/me").status_code == 401


def test_login_requires_json_body(monkeypatch):
    c, _ = make(monkeypatch)
    r = c.post("/api/login", data="username=ana&password=x",
               headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 422


def test_security_headers(monkeypatch):
    c, _ = make(monkeypatch)
    h = c.get("/").headers
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in h["content-security-policy"]


def test_no_openapi_exposed(monkeypatch):
    c, _ = make(monkeypatch)
    assert c.get("/openapi.json").status_code == 404 and c.get("/docs").status_code == 404
