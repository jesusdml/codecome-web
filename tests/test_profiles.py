# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import Auth, hash_password
from app.itemdb import ItemDB
from app.jobs import JobDB
from app.profiles import Profiles, ProfileError
from app.runner import Runner
from tests.test_api import FIX, PW

FAKE = str(Path(__file__).parent / "fake_phase.py")
GOOD = {"default": "mock", "profiles": {
    "mock": {"enabled": True, "env": {"CODECOME_MODEL": "test/mockmodel", "MOCKLLM_PORT": "18080"}},
    "local": {"enabled": False, "env": {"CODECOME_MODEL": "local-ollama/x"}},
}}


def mk(tmp_path, data):
    p = tmp_path / "profiles.json"
    p.write_text(json.dumps(data) if not isinstance(data, str) else data)
    return Profiles(p)


def test_missing_file_gives_a_single_default_profile(tmp_path):
    pr = Profiles(tmp_path / "no.json")
    assert pr.resolve(None) == "default" and pr.env(None) == {}


def test_resolve_default_named_unknown_and_disabled(tmp_path):
    pr = mk(tmp_path, GOOD)
    assert pr.resolve(None) == "mock" and pr.resolve("mock") == "mock"
    with pytest.raises(ProfileError) as e:
        pr.resolve("nope")
    assert e.value.code == "profile_not_found"
    with pytest.raises(ProfileError) as e:
        pr.resolve("local")
    assert e.value.code == "profile_disabled" and e.value.params == {"name": "local"}


def test_public_view_never_contains_variables(tmp_path):
    pub = mk(tmp_path, GOOD).public()
    nodef = {k: None for k in "123456"}
    assert pub == {"default": "mock", "models": [], "profiles": [
        {"name": "mock", "enabled": True, "models": nodef}, {"name": "local", "enabled": False, "models": nodef}]}
    assert "test/mockmodel" not in json.dumps(pub)


@pytest.mark.parametrize("bad", [
    {"default": "a", "profiles": {"a": {"env": {"PATH": "/tmp/evil"}}}},
    {"default": "a", "profiles": {"a": {"env": {"LD_PRELOAD": "/tmp/x.so"}}}},
    {"default": "a", "profiles": {"a": {"env": {"HOME": "/root"}}}},
    {"default": "a", "profiles": {"a": {"env": {"codecome_model": "x"}}}},
    {"default": "a", "profiles": {"a": {"env": {"CODECOME_MODEL": "x\nPATH=/evil"}}}},
    {"default": "a", "profiles": {"a": {"env": {"CODECOME_MODEL": "x" * 600}}}},
    {"default": "a", "profiles": {"a": {"env": {"CODECOME_MODEL": 5}}}},
    {"default": "a", "profiles": {"a": {"env": ["CODECOME_X"]}}},
    {"default": "b", "profiles": {"a": {"env": {}}}},
    {"default": "A", "profiles": {"A": {"env": {}}}},
    {"default": "../x", "profiles": {"../x": {"env": {}}}},
    {"default": "a", "profiles": {}},
    {"default": "a"},
    [],
])
def test_dangerous_or_malformed_files_are_rejected(tmp_path, bad):
    with pytest.raises(ProfileError) as e:
        mk(tmp_path, bad).public()
    assert e.value.code == "profiles_invalid"


def test_broken_json_is_rejected(tmp_path):
    with pytest.raises(ProfileError):
        mk(tmp_path, "{no es json").public()


def test_file_is_reread_without_restart(tmp_path):
    pr = mk(tmp_path, GOOD)
    assert pr.env("mock")["MOCKLLM_PORT"] == "18080"
    (tmp_path / "profiles.json").write_text(json.dumps({**GOOD, "profiles": {"mock": {"env": {"MOCKLLM_PORT": "19999"}}}}))
    assert pr.env("mock")["MOCKLLM_PORT"] == "19999"


def test_repo_profiles_file_is_valid_and_translated():
    cfg = Path(__file__).resolve().parent.parent / "config"
    # this installation's file if there is one; the published example otherwise (public branch)
    pr = Profiles(cfg / "profiles.json" if (cfg / "profiles.json").exists() else cfg / "profiles.example.json")
    names = [p["name"] for p in pr.public()["profiles"]]
    en = json.loads((Path(main.__file__).parent / "locales" / "en.json").read_text(encoding="utf-8"))
    assert pr.public()["default"] in names and all(f"profile.{n}" in en for n in names)


# ---------- ejecutor ----------
def run_env_job(tmp_path, profiles, profile, monkeypatch=None):
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    db = JobDB(tmp_path / "jobs.db")
    runner = Runner(db, root, profiles=profiles, argv_builder=lambda a, f: [sys.executable, FAKE, "env"], interval=0.1)
    jid = db.enqueue("phase-1", None, "ana", ItemDB(FIX), profile)
    status = runner.run(db.claim_next(tmp_path / "logs"))
    return db, jid, status


def test_profile_variables_reach_the_process_and_secrets_do_not_leak(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "worker-key")  # passes (allowed prefix): it is the worker's
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "no-debe-pasar")
    db, jid, status = run_env_job(tmp_path, mk(tmp_path, GOOD), "mock")
    assert status == "done"
    out = Path(db.get(jid)["log_path"]).read_text()
    assert "MODEL=test/mockmodel PORT=18080" in out and "STALL=None" in out
    assert "no-debe-pasar" not in out and db.get(jid)["profile"] == "mock"


def test_job_fails_cleanly_if_profile_disappears_before_start(tmp_path):
    pr = mk(tmp_path, GOOD)
    db = JobDB(tmp_path / "jobs.db")
    root = tmp_path / "root"
    root.mkdir()
    runner = Runner(db, root, profiles=pr, argv_builder=lambda a, f: [sys.executable, FAKE, "env"], interval=0.1)
    jid = db.enqueue("phase-1", None, "ana", ItemDB(FIX), "mock")
    (tmp_path / "profiles.json").write_text(json.dumps({"default": "x", "profiles": {"x": {"env": {}}}}))
    assert runner.run(db.claim_next(tmp_path / "logs")) == "failed"
    e = db.get(jid)["error"]
    assert e["code"] == "profile_unavailable" and e["params"]["reason"] == "profile_not_found"


# ---------- base de datos ----------
def test_old_database_without_profile_column_is_migrated(tmp_path):
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, finding TEXT, status TEXT NOT NULL,"
              " requested_by TEXT NOT NULL, created_at REAL NOT NULL, started_at REAL, finished_at REAL, exit_code INTEGER,"
              " error TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0, log_path TEXT, live TEXT)")
    c.execute("INSERT INTO jobs(action,status,requested_by,created_at) VALUES('phase-1','done','ana',1)")
    c.commit(); c.close()
    db = JobDB(p)
    assert db.get(1)["profile"] is None
    assert db.enqueue("phase-2", None, "ana", ItemDB(FIX), "mock")
    JobDB(p)  # abrir otra vez no falla (la columna ya existe)


# ---------- API ----------
@pytest.fixture()
def c(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "db", ItemDB(FIX))
    monkeypatch.setattr(main, "auth", Auth({"ana": hash_password(PW)}, b"k" * 32))
    monkeypatch.setattr(main, "jobs", JobDB(tmp_path / "jobs.db"))
    monkeypatch.setattr(main, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(main, "profiles", mk(tmp_path, GOOD))
    cl = TestClient(main.app)
    assert cl.post("/api/login", json={"username": "ana", "password": PW}).status_code == 200
    return cl


def test_api_lists_profiles_without_variables(c):
    d = c.get("/api/jobs").json()
    assert d["default_profile"] == "mock" and [p["name"] for p in d["profiles"]] == ["mock", "local"]
    assert "test/mockmodel" not in json.dumps(d) and "18080" not in json.dumps(d)


def test_api_uses_default_and_stores_the_profile(c):
    jid = c.post("/api/jobs", json={"action": "phase-1"}).json()["id"]
    assert c.get(f"/api/jobs/{jid}").json()["job"]["profile"] == "mock"


@pytest.mark.parametrize("profile,code", [("nope", "profile_not_found"), ("local", "profile_disabled"),
                                          ("../etc", "profile_not_found"), ("MOCK", "profile_not_found")])
def test_api_rejects_unknown_or_disabled_profiles(c, profile, code):
    r = c.post("/api/jobs", json={"action": "phase-1", "profile": profile})
    assert r.status_code == 400 and r.json()["detail"]["code"] == code


def test_api_cannot_inject_variables_through_the_body(c):
    r = c.post("/api/jobs", json={"action": "phase-1", "profile": "mock", "env": {"PATH": "/evil"}})
    assert r.status_code == 201  # campo extra ignorado
    assert "PATH" not in json.dumps(c.get("/api/jobs").json())


def test_api_reports_invalid_profiles_file(c, tmp_path):
    (tmp_path / "profiles.json").write_text("{roto")
    assert c.get("/api/jobs").json()["detail"]["code"] == "profiles_invalid"
    assert c.post("/api/jobs", json={"action": "phase-1"}).json()["detail"]["code"] == "profiles_invalid"


# ---------- per-phase overrides ----------
PHASED = {"default": "api", "profiles": {"api": {"enabled": True,
    "env": {"CODECOME_MODEL": "anthropic/cheap", "CODECOME_BUSY_STALL_TIMEOUT": "600"},
    "phase_env": {"5": {"CODECOME_MODEL": "anthropic/strong"}}}}}


def test_phase_env_overrides_only_its_phase(tmp_path):
    pr = mk(tmp_path, PHASED)
    assert pr.env("api", "phase-1")["CODECOME_MODEL"] == "anthropic/cheap"
    assert pr.env("api", "phase-4")["CODECOME_MODEL"] == "anthropic/cheap"
    five = pr.env("api", "phase-5")
    assert five["CODECOME_MODEL"] == "anthropic/strong" and five["CODECOME_BUSY_STALL_TIMEOUT"] == "600"
    assert pr.env("api")["CODECOME_MODEL"] == "anthropic/cheap"
    assert "phase_env" not in json.dumps(pr.public())


@pytest.mark.parametrize("phase_env", [{"9": {}}, {"5": {"LD_PRELOAD": "x"}}, {"5": "x"}, ["5"]])
def test_bad_phase_env_is_rejected(tmp_path, phase_env):
    data = json.loads(json.dumps(PHASED))
    data["profiles"]["api"]["phase_env"] = phase_env
    with pytest.raises(ProfileError):
        mk(tmp_path, data).env("api")


def test_runner_applies_the_phase_override(tmp_path):
    pr = mk(tmp_path, PHASED)
    root = tmp_path / "root"; root.mkdir()
    db = JobDB(tmp_path / "jobs.db")
    runner = Runner(db, root, profiles=pr, argv_builder=lambda a, f: [sys.executable, FAKE, "env"], interval=0.1)
    jid = db.enqueue("phase-1", None, "ana", ItemDB(FIX), "api")
    runner.run(db.claim_next(tmp_path / "logs"))
    log = Path(db.get(jid)["log_path"]).read_text()
    assert "anthropic/cheap" in log and "anthropic/strong" not in log


# ---------- model catalog: the browser picks a catalog NAME per phase ----------
CAT = {"default": "api", "profiles": {"api": {"enabled": True,
    "env": {"CODECOME_MODEL": "anthropic/cheap", "CODECOME_BUSY_STALL_TIMEOUT": "600"},
    "phase_env": {"5": {"CODECOME_MODEL": "anthropic/strong"}}}},
    "models": {"cheap": {"label": "Cheap", "env": {"CODECOME_MODEL": "anthropic/cheap"}},
               "strong": {"label": "Strong", "env": {"CODECOME_MODEL": "anthropic/strong"}},
               "lynx": {"label": "Lynx (local)", "phases": ["4", "5"], "env": {"CODECOME_MODEL": "local/lynx"}},
               "off": {"label": "Off", "enabled": False, "env": {"CODECOME_MODEL": "x/y"}}}}


def test_public_catalog_and_defaults_without_model_ids(tmp_path):
    pub = mk(tmp_path, CAT).public()
    assert [m["name"] for m in pub["models"]] == ["cheap", "strong", "lynx"]  # disabled ones hidden
    assert pub["models"][2]["phases"] == ["4", "5"]
    assert pub["profiles"][0]["models"] == {"1": "cheap", "2": "cheap", "3": "cheap", "4": "cheap", "5": "strong", "6": "cheap"}
    assert "anthropic/" not in json.dumps(pub) and "local/lynx" not in json.dumps(pub)


def test_model_choice_overrides_profile_and_is_checked(tmp_path):
    pr = mk(tmp_path, CAT)
    assert pr.env("api", "phase-2", "strong")["CODECOME_MODEL"] == "anthropic/strong"
    assert pr.env("api", "phase-4", "lynx")["CODECOME_MODEL"] == "local/lynx"
    assert pr.env("api", "phase-4", "lynx")["CODECOME_BUSY_STALL_TIMEOUT"] == "600"
    for model, action, code in (("lynx", "phase-2", "model_not_allowed"), ("off", "phase-1", "model_not_found"),
                                ("nope", "phase-1", "model_not_found")):
        with pytest.raises(ProfileError) as e:
            pr.check_model(model, action)
        assert e.value.code == code


@pytest.mark.parametrize("models", [{"Bad Name": {"env": {"CODECOME_MODEL": "a/b"}}},
                                    {"m": {"env": {}}}, {"m": {"env": {"PATH": "x", "CODECOME_MODEL": "a"}}},
                                    {"m": {"phases": ["9"], "env": {"CODECOME_MODEL": "a"}}}, ["m"]])
def test_bad_catalogs_are_rejected(tmp_path, models):
    data = json.loads(json.dumps(CAT)); data["models"] = models
    with pytest.raises(ProfileError):
        mk(tmp_path, data).public()


def test_api_stores_the_model_and_rejects_bad_ones(c, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "profiles", mk(tmp_path, {**CAT, "default": "api"}))
    ok = c.post("/api/jobs", json={"action": "phase-1", "model": "strong"})
    assert ok.status_code == 201
    assert main.jobs.get(ok.json()["id"])["model"] == "strong"
    assert c.post("/api/jobs", json={"action": "phase-2", "model": "lynx"}).json()["detail"]["code"] == "model_not_allowed"
    assert c.post("/api/jobs", json={"action": "phase-2", "model": "anthropic/strong"}).status_code in (400, 422)
