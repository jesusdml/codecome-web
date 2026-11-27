# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import Auth, hash_password
from app.profiles import Profiles
from app.settings import Settings, SettingsError, validate
from tests.test_api import PW

CAT = {"default": "api", "profiles": {
    "api": {"env": {"CODECOME_MODEL": "anthropic/cheap"}, "phase_env": {"5": {"CODECOME_MODEL": "anthropic/strong"}}},
    "mock": {"env": {"CODECOME_MODEL": "test/mock"}}},
    "models": {"cheap": {"label": "Cheap", "env": {"CODECOME_MODEL": "anthropic/cheap"}},
               "strong": {"label": "Strong", "env": {"CODECOME_MODEL": "anthropic/strong"}},
               "lynx": {"label": "Lynx", "local": True, "phases": ["4", "5"], "env": {"CODECOME_MODEL": "local/lynx"}}}}
LANGS = ("en", "es")


def setup(tmp_path, data=CAT):
    p = tmp_path / "profiles.json"; p.write_text(json.dumps(data))
    s = Settings(tmp_path / "data" / "settings.json")
    return Profiles(p, s, LANGS), s


def v(data):
    return validate(data, catalog={"cheap", "strong", "lynx"}, profiles={"api", "mock"}, langs=LANGS)


@pytest.mark.parametrize("bad", [
    {"default_lang": "xx"}, {"models": {"nope": {"enabled": True}}}, {"models": {"cheap": {"env": {"X": "y"}}}},
    {"models": {"cheap": {"phases": ["7"]}}}, {"models": {"cheap": {"enabled": "yes"}}},
    {"profile_models": {"api": {"1": "anthropic/claude-opus-5-5"}}}, {"profile_models": {"ghost": {"1": "cheap"}}},
    {"profile_models": {"api": {"9": "cheap"}}}, {"something_else": 1}, ["x"]])
def test_settings_only_choose_among_server_lists(bad):
    with pytest.raises(SettingsError):
        v(bad)


def test_overlay_changes_catalog_defaults_and_the_env_a_job_gets(tmp_path):
    pr, s = setup(tmp_path)
    s.write(v({"models": {"lynx": {"phases": ["1", "4", "5"]}, "strong": {"enabled": False}},
               "profile_models": {"api": {"1": "lynx", "2": "strong"}}}), "ana")
    pub = pr.public()
    assert [m["name"] for m in pub["models"]] == ["cheap", "lynx"]          # strong disabled
    assert pub["models"][1] == {"name": "lynx", "label": "Lynx", "phases": ["1", "4", "5"], "local": True}
    api = next(p for p in pub["profiles"] if p["name"] == "api")["models"]
    assert api["1"] == "lynx" and api["2"] == "cheap"   # "strong" is disabled, so its default is not applied
    assert pr.env("api", "phase-1")["CODECOME_MODEL"] == "local/lynx"     # a job without a pick uses the default
    assert pr.env("mock", "phase-1")["CODECOME_MODEL"] == "test/mock"     # other profiles untouched
    assert "local/lynx" not in json.dumps(pub) and "anthropic/" not in json.dumps(pr.admin_view())


def test_a_broken_or_stale_settings_file_never_breaks_launches(tmp_path):
    pr, s = setup(tmp_path)
    s.path.parent.mkdir(parents=True, exist_ok=True)
    s.path.write_text('{"profile_models": {"api": {"1": "removed-model"}}}')
    assert pr.env("api", "phase-1")["CODECOME_MODEL"] == "anthropic/cheap"
    s.path.write_text("not json")
    assert pr.env("api", "phase-5")["CODECOME_MODEL"] == "anthropic/strong"


def test_write_is_atomic_and_group_readable(tmp_path):
    _, s = setup(tmp_path)
    s.write(v({"default_lang": "es"}), "ana")
    assert oct(s.path.stat().st_mode & 0o777) == "0o660"
    assert json.loads(s.path.read_text())["updated_by"] == "ana"
    assert not list(s.path.parent.glob(".settings-*"))


@pytest.fixture()
def api(monkeypatch, tmp_path):
    pr, s = setup(tmp_path)
    monkeypatch.setattr(main, "profiles", pr)
    monkeypatch.setattr(main, "settings", s)
    monkeypatch.setattr(main, "ADMINS", set())
    monkeypatch.setattr(main, "auth", Auth({"ana": hash_password(PW), "bob": hash_password(PW)}, b"k" * 32))
    return TestClient(main.app)


def login(c, user="ana"):
    assert c.post("/api/login", json={"username": user, "password": PW}).status_code == 200


def test_api_get_put_and_public_default_language(api, monkeypatch):
    assert api.get("/api/settings").status_code == 401
    assert api.get("/api/ui").json() == {"default_lang": "en", "langs": list(main.LANGS)}
    login(api)
    d = api.get("/api/settings").json()
    assert d["can_edit"] and [m["name"] for m in d["catalog"]] == ["cheap", "strong", "lynx"]
    assert "env" not in json.dumps(d) and "anthropic/" not in json.dumps(d)
    r = api.put("/api/settings", json={"default_lang": "es", "profile_models": {"api": {"3": "strong"}}})
    assert r.status_code == 200 and r.json()["updated_by"] == "ana"
    assert api.get("/api/ui").json()["default_lang"] == "es"
    assert main.profiles.env("api", "phase-3")["CODECOME_MODEL"] == "anthropic/strong"
    bad = api.put("/api/settings", json={"profile_models": {"api": {"1": "anthropic/x"}}})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "settings_invalid"


def test_only_admins_can_change_when_admins_are_set(api, monkeypatch):
    monkeypatch.setattr(main, "ADMINS", {"ana"})
    login(api, "bob")
    assert api.get("/api/settings").json()["can_edit"] is False
    r = api.put("/api/settings", json={"default_lang": "es"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "settings_forbidden"


def test_put_needs_same_origin(api):
    login(api)
    r = api.put("/api/settings", json={"default_lang": "es"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_active_profile_becomes_the_default_for_launches(tmp_path):
    pr, s = setup(tmp_path)
    assert pr.resolve(None) == "api"
    s.write(v({"active_profile": "mock"}), "ana")
    assert pr.resolve(None) == "mock" and pr.public()["default"] == "mock"
    assert pr.env(None, "phase-1")["CODECOME_MODEL"] == "test/mock"
    with pytest.raises(SettingsError):
        v({"active_profile": "ghost"})


def test_auditors_cannot_pick_another_profile(api, monkeypatch, tmp_path):
    from app.itemdb import ItemDB
    from app.jobs import JobDB
    monkeypatch.setattr(main, "ADMINS", {"ana"})
    monkeypatch.setattr(main, "jobs", JobDB(tmp_path / "jobs.db"))
    monkeypatch.setattr(main, "db", ItemDB(tmp_path))
    login(api, "bob")
    r = api.post("/api/jobs", json={"action": "phase-1", "profile": "mock"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "profile_forbidden"
    assert api.post("/api/jobs", json={"action": "phase-1"}).status_code == 201    # the active one
    assert main.jobs.list_jobs(None)[0]["profile"] == "api"
