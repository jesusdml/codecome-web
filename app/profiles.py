# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Run profiles: a name -> the environment variables a phase is launched with.

The browser only sends the NAME. The variables come from a server-side file and only
keys with known prefixes are accepted (never PATH, LD_PRELOAD...). API keys do NOT go
here: they live in the worker's EnvironmentFile.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .jobs import CodedError

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
ENV_PREFIXES = ("CODECOME_", "OPENCODE_", "MOCKLLM_", "OLLAMA_")
MAX_VALUE = 512
PHASE_KEYS = ("1", "2", "3", "4", "5", "6")

DEFAULT = {"default": "default", "profiles": {"default": {"enabled": True, "env": {}, "phase_env": {}}}, "models": {}}


class ProfileError(CodedError):
    pass


def _check_env(env, name: str) -> dict:
    if not isinstance(env, dict):
        raise ProfileError("profiles_invalid", reason="env", name=name)
    for k, v in env.items():
        if not ENV_KEY_RE.fullmatch(str(k)) or not str(k).startswith(ENV_PREFIXES):
            raise ProfileError("profiles_invalid", reason="env_key", name=name, key=str(k))
        if not isinstance(v, str) or len(v) > MAX_VALUE or any(c in v for c in "\0\n\r"):
            raise ProfileError("profiles_invalid", reason="env_value", name=name, key=str(k))
    return dict(env)


def _validate(data) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict) or not data["profiles"]:
        raise ProfileError("profiles_invalid", reason="structure")
    out = {}
    for name, p in data["profiles"].items():
        if not NAME_RE.fullmatch(str(name)) or not isinstance(p, dict):
            raise ProfileError("profiles_invalid", reason="name", name=str(name))
        env = _check_env(p.get("env", {}), name)
        # Per-phase overrides on top of `env`, e.g. a stronger model only for the exploit phase.
        phase_env = p.get("phase_env", {})
        if not isinstance(phase_env, dict) or any(k not in PHASE_KEYS for k in phase_env):
            raise ProfileError("profiles_invalid", reason="phase_env", name=name)
        out[name] = {"enabled": bool(p.get("enabled", True)), "env": env,
                     "phase_env": {k: _check_env(v, name) for k, v in phase_env.items()}}
    default = data.get("default")
    if default not in out:
        raise ProfileError("profiles_invalid", reason="default")
    # Model catalog: what the browser may pick per phase. It only ever sends a catalog NAME.
    models = {}
    raw = data.get("models", {})
    if not isinstance(raw, dict):
        raise ProfileError("profiles_invalid", reason="models")
    for mname, m in raw.items():
        if not NAME_RE.fullmatch(str(mname)) or not isinstance(m, dict):
            raise ProfileError("profiles_invalid", reason="model_name", name=str(mname))
        phases = m.get("phases", list(PHASE_KEYS))
        label = m.get("label", mname)
        env = _check_env(m.get("env", {}), mname)
        if (not isinstance(phases, list) or any(k not in PHASE_KEYS for k in phases)
                or not isinstance(label, str) or len(label) > 60 or "CODECOME_MODEL" not in env):
            raise ProfileError("profiles_invalid", reason="model", name=mname)
        models[mname] = {"label": label, "phases": [k for k in PHASE_KEYS if k in phases],
                         "enabled": bool(m.get("enabled", True)), "env": env,
                         "local": bool(m.get("local", False)), "provider": str(m.get("provider", ""))[:30]}
    return {"default": default, "profiles": out, "models": models}


class Profiles:
    def __init__(self, path: Path, settings=None, langs: tuple[str, ...] = ("en", "es")):
        self.path = Path(path)
        self.settings = settings  # app.settings.Settings or None
        self.langs = langs

    def base(self) -> dict:
        """The server file alone, without the web settings."""
        if not self.path.exists():
            return json.loads(json.dumps(DEFAULT))
        try:
            return _validate(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            if isinstance(e, ProfileError):
                raise
            raise ProfileError("profiles_invalid", reason="unreadable")

    def _read(self) -> dict:
        """Re-read on every use: editing the file needs no restart."""
        d = self.base()
        if self.settings is not None:
            self._overlay(d, self.settings.read_raw())
        return d

    def _overlay(self, d: dict, raw: dict) -> None:
        # Web settings may only toggle/restrict catalog entries and pick defaults among them.
        from .settings import SettingsError, validate
        try:
            s = validate({k: raw[k] for k in ("default_lang", "active_profile", "models", "profile_models") if k in raw},
                         catalog=set(d["models"]), profiles=set(d["profiles"]), langs=self.langs)
        except SettingsError:
            return  # a stale or broken settings file never breaks launches: the server file rules
        ap = s["active_profile"]
        if ap and d["profiles"][ap]["enabled"]:
            d["default"] = ap  # the profile every launch uses (chosen on the Settings page)
        for name, m in s["models"].items():
            d["models"][name].update(m)
        for prof, per in s["profile_models"].items():
            for k, name in per.items():
                m = d["models"][name]
                if m["enabled"] and k in m["phases"]:
                    pe = d["profiles"][prof]["phase_env"]
                    pe[k] = {**pe.get(k, {}), **m["env"]}

    def default_lang(self) -> str | None:
        if self.settings is None:
            return None
        lang = self.settings.read_raw().get("default_lang")
        return lang if lang in self.langs else None

    def public(self) -> dict:
        """What the browser sees: names and whether they can be used. Never the variables."""
        d = self._read()
        by_model = {m["env"]["CODECOME_MODEL"]: n for n, m in d["models"].items() if m["enabled"]}

        def defaults(p):  # which catalog entry each phase uses when the user picks nothing
            base = p["env"].get("CODECOME_MODEL")
            return {k: by_model.get(p["phase_env"].get(k, {}).get("CODECOME_MODEL", base)) for k in PHASE_KEYS}

        return {"default": d["default"],
                "profiles": [{"name": n, "enabled": p["enabled"], "models": defaults(p)} for n, p in d["profiles"].items()],
                "models": [{"name": n, "label": m["label"], "phases": m["phases"], "local": m["local"]}
                           for n, m in d["models"].items() if m["enabled"]]}

    def admin_view(self) -> dict:
        """For the Settings page: the whole catalog (also disabled entries), still without env or ids."""
        d = self._read()
        pub = self.public()
        return {"catalog": [{"name": n, "label": m["label"], "local": m["local"], "provider": m["provider"],
                             "enabled": m["enabled"], "phases": m["phases"]} for n, m in d["models"].items()],
                "profiles": pub["profiles"], "default_profile": pub["default"]}

    def resolve(self, name: str | None) -> str:
        d = self._read()
        name = name or d["default"]
        if name not in d["profiles"]:
            raise ProfileError("profile_not_found")
        if not d["profiles"][name]["enabled"]:
            raise ProfileError("profile_disabled", name=name)
        return name

    def check_model(self, model: str | None, action: str) -> str | None:
        if model is None:
            return None
        m = self._read()["models"].get(model)
        if m is None or not m["enabled"]:
            raise ProfileError("model_not_found")
        if action.removeprefix("phase-") not in m["phases"]:
            raise ProfileError("model_not_allowed", model=model)
        return model

    def env(self, name: str | None, action: str | None = None, model: str | None = None) -> dict[str, str]:
        d = self._read()
        p = d["profiles"][self.resolve(name)]
        out = dict(p["env"])
        if action:
            out.update(p["phase_env"].get(action.removeprefix("phase-"), {}))
        if model is not None:  # the user's pick for this job wins over the profile default
            self.check_model(model, action or "")
            out.update(d["models"][model]["env"])
        return out
