# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Settings editable from the web (the Settings page).

Only choices among things the server already defines: which catalog models are enabled and in which
phases, the default model of each phase per profile, and the default UI language. Model ids and
environment variables stay in config/profiles.json (server-side file); the browser can never add them.

Stored in data/settings.json (the web writes it, the worker reads it), replaced atomically.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

PHASE_KEYS = ("1", "2", "3", "4", "5", "6")
EMPTY = {"default_lang": None, "active_profile": None, "models": {}, "profile_models": {}}


class SettingsError(ValueError):
    def __init__(self, code: str, **params):
        super().__init__(code)
        self.code, self.params = code, params


def validate(data, *, catalog: set[str], profiles: set[str], langs: tuple[str, ...]) -> dict:
    """Everything is checked against closed lists; unknown keys are an error, not ignored."""
    if not isinstance(data, dict) or set(data) - {"default_lang", "active_profile", "models", "profile_models"}:
        raise SettingsError("settings_invalid", reason="structure")
    out = {"default_lang": None, "active_profile": None, "models": {}, "profile_models": {}}
    ap = data.get("active_profile")
    if ap is not None:
        if ap not in profiles:
            raise SettingsError("settings_invalid", reason="active_profile")
        out["active_profile"] = ap
    lang = data.get("default_lang")
    if lang is not None:
        if lang not in langs:
            raise SettingsError("settings_invalid", reason="lang")
        out["default_lang"] = lang
    models = data.get("models", {})
    if not isinstance(models, dict):
        raise SettingsError("settings_invalid", reason="models")
    for name, m in models.items():
        if name not in catalog or not isinstance(m, dict) or set(m) - {"enabled", "phases"}:
            raise SettingsError("settings_invalid", reason="model", name=str(name)[:40])
        entry = {}
        if "enabled" in m:
            if not isinstance(m["enabled"], bool):
                raise SettingsError("settings_invalid", reason="model", name=name)
            entry["enabled"] = m["enabled"]
        if "phases" in m:
            ph = m["phases"]
            if not isinstance(ph, list) or any(k not in PHASE_KEYS for k in ph):
                raise SettingsError("settings_invalid", reason="phases", name=name)
            entry["phases"] = [k for k in PHASE_KEYS if k in ph]
        out["models"][name] = entry
    pm = data.get("profile_models", {})
    if not isinstance(pm, dict):
        raise SettingsError("settings_invalid", reason="profile_models")
    for prof, per in pm.items():
        if prof not in profiles or not isinstance(per, dict):
            raise SettingsError("settings_invalid", reason="profile", name=str(prof)[:40])
        clean = {}
        for k, name in per.items():
            if k not in PHASE_KEYS or name not in catalog:
                raise SettingsError("settings_invalid", reason="default", name=str(name)[:40])
            clean[k] = name
        out["profile_models"][prof] = clean
    return out


class Settings:
    def __init__(self, path: Path):
        self.path = Path(path)

    def read_raw(self) -> dict:
        """Unvalidated on purpose: the catalog may have changed since it was saved. Profiles applies
        only what still makes sense (see Profiles._overlay)."""
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else dict(EMPTY)
        except (OSError, ValueError):
            return dict(EMPTY)

    def write(self, data: dict, user: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(data, updated_by=user, updated_at=time.time())
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".settings-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=1, ensure_ascii=False)
            os.chmod(tmp, 0o660)  # the worker (another user, same group) must read it
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
