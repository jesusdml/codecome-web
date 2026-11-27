# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Language consistency: no loose text, and every language with the same keys."""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import Auth, hash_password
from app.itemdb import STATUSES
from app.jobs import ACTIONS, ACTIVE, FINAL
from tests.test_api import PW

APP = Path(__file__).resolve().parent.parent / "app"
LOCALES = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (APP / "locales").glob("*.json")}
PLACEHOLDER = re.compile(r"\{(\w+)\}")
HTML = "\n".join(p.read_text(encoding="utf-8") for p in (APP / "static" / "js").rglob("*.js"))
PY = {p.name: p.read_text(encoding="utf-8") for p in APP.glob("*.py")}


def test_english_and_spanish_exist_and_match_main_langs():
    assert {"en", "es"} <= set(LOCALES) and set(LOCALES) == set(main.LANGS)


def test_same_keys_in_every_language():
    en = set(LOCALES["en"])
    for lang, d in LOCALES.items():
        assert set(d) == en, (lang, sorted(set(d) ^ en))


def test_same_placeholders_and_no_empty_text():
    for lang, d in LOCALES.items():
        for k, v in d.items():
            assert v.strip(), (lang, k)
            assert set(PLACEHOLDER.findall(v)) == set(PLACEHOLDER.findall(LOCALES["en"][k])), (lang, k)


def test_every_literal_key_used_by_the_page_exists():
    used = set(re.findall(r"\bt\(\"([a-z_.\-A-Z0-9]+)\"", HTML))
    used |= set(re.findall(r"\[\"[a-z]+\",\s*\"(tab\.[a-z]+)\"\]", HTML))
    assert used, "the page does not use t()"
    # Dynamic keys (t("box." + k)) arrive as a prefix: the families test covers them.
    missing = sorted(k for k in used if not k.endswith(".") and k not in LOCALES["en"])
    assert not missing, missing


def test_dynamic_key_families_are_complete():
    en = LOCALES["en"]
    need = {f"fstatus.{s}" for s in STATUSES} | {f"phase.{a}" for a in ACTIONS}
    need |= {f"jstatus.{s}" for s in ACTIVE + FINAL}
    need |= {f"state.{s}" for s in ("generating", "working", "waiting_model", "quiet", "retrying", "stalled", "stopping", "finished")}
    need |= {f"reason.{s}" for s in ("generating", "working", "waiting_model", "quiet", "retrying", "stalled", "stopping", "finished")}
    need |= {f"box.{k}" for k in ("state", "time", "cpu", "memory", "agent", "model", "last_step", "host")}
    need |= {f"jobs.col.{c}" for c in ("id", "phase", "finding", "status", "now", "by")}
    need |= {f"findings.col.{c}" for c in ("id", "title", "severity", "confidence")}
    assert not sorted(need - set(en)), sorted(need - set(en))


def test_every_error_code_raised_by_the_server_has_a_translation():
    codes = set()
    codes |= set(re.findall(r'api_error\(\d+, "(\w+)"', PY["main.py"]))
    codes |= set(re.findall(r'(?:InvalidJob|DuplicateJob)\("(\w+)"', PY["jobs.py"]))
    codes |= set(re.findall(r'\berr\("(\w+)"', PY["jobs.py"] + PY["runner.py"]))
    codes |= set(re.findall(r'ProfileError\("(\w+)"', PY["profiles.py"]))
    codes |= {"invalid_finding_id"}
    # v1.1 maintenance: worker handlers, one-shot secrets, uploads and every extractor refusal
    codes |= set(re.findall(r'MaintError\("(\w+)"', PY["maintenance.py"] + PY["archive.py"]))
    codes |= set(re.findall(r'GpgError\("(\w+)"', PY["gpgbox.py"]))
    codes |= set(re.findall(r'SecretError\("(\w+)"', PY["oneshot.py"]))
    codes |= set(re.findall(r'UploadError\("(\w+)"', PY["uploads.py"]))
    codes |= set(re.findall(r'StoreError\("(\w+)"', PY["archive_store.py"]))
    codes |= set(re.findall(r'ProjectNameError\("(\w+)"', PY["project_cfg.py"]))
    codes |= {"extract_" + c for c in re.findall(r'ExtractError\("(\w+)"', PY["safe_extract.py"])}
    codes.discard("extract_")  # the runner's err("extract_" + code) prefix itself
    assert {"extract_bad_password", "extract_zip_aes", "src_not_empty", "secret_missing"} <= codes
    assert codes
    missing = sorted(c for c in codes if f"error.{c}" not in LOCALES["en"])
    assert not missing, missing


def test_server_has_no_spanish_user_text_left():
    # User-facing text no longer lives on the server (these Spanish strings must NOT appear).
    for name in ("main.py", "jobs.py", "runner.py", "monitor.py"):
        for bad in ("no autenticado", "credenciales", "no válido", "no encontrado", "superó", "esperando al modelo"):
            assert bad not in PY[name], (name, bad)


@pytest.fixture()
def c(monkeypatch):
    monkeypatch.setattr(main, "auth", Auth({"ana": hash_password(PW)}, b"k" * 32))
    return TestClient(main.app)


def test_locale_endpoint(c):
    r = c.get("/locales/en.json")
    assert r.status_code == 200 and r.json()["lang.name"] == "English"
    assert c.get("/locales/es.json").json()["lang.name"] == "Español"
    for bad in ("xx", "..%2Fauth", "en.json", "EN"):
        assert c.get(f"/locales/{bad}.json").status_code == 404


def test_api_errors_carry_codes_not_text(c):
    r = c.post("/api/login", json={"username": "ana", "password": "mala"})
    assert r.status_code == 401 and r.json()["detail"] == {"code": "bad_credentials", "params": {}}
    assert c.get("/api/me").json()["detail"]["code"] == "not_authenticated"
