# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Authentication: users in a file (scrypt) + a signed session cookie (HMAC).

No external dependencies. Passwords are never stored in clear and never travel
through chat: they are created with tools/adduser.py (getpass).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

_N, _R, _P = 2**14, 8, 1
COOKIE = "cc_session"
SESSION_TTL = 8 * 3600
MAX_FAILS = 5
LOCKOUT_S = 300


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (
        _N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode(),
    )


def _verify_hash(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


# Hash for a non-existent user: same response time either way.
_DUMMY = hash_password(secrets.token_hex(8))


def load_secret(path: Path) -> bytes:
    """Signing key: env CODECOME_SECRET, or a 0600 file created when missing."""
    env = os.environ.get("CODECOME_SECRET")
    if env:
        return env.encode()
    if not path.exists():
        path.write_bytes(secrets.token_bytes(32))
        path.chmod(0o600)
    return path.read_bytes()


def load_users(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.items()}


class Auth:
    def __init__(self, users: dict[str, str], secret: bytes, *, secure_cookie: bool = False,
                 now=time.time):
        self.users = users
        self.secret = secret
        self.secure_cookie = secure_cookie
        self._now = now
        self._fails: dict[str, tuple[int, float]] = {}

    @classmethod
    def from_env(cls, data_dir: Path) -> "Auth":
        data_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            load_users(Path(os.environ.get("CODECOME_USERS", data_dir / "users.json"))),
            load_secret(data_dir / "secret.key"),
            secure_cookie=os.environ.get("CODECOME_COOKIE_SECURE") == "1",
        )

    # --- attempt limit (per IP + user) ---
    def _key(self, ip: str, user: str) -> str:
        return f"{ip}|{user}"

    def locked(self, ip: str, user: str) -> bool:
        n, until = self._fails.get(self._key(ip, user), (0, 0.0))
        return n >= MAX_FAILS and self._now() < until

    def _fail(self, ip: str, user: str) -> None:
        k = self._key(ip, user)
        n, until = self._fails.get(k, (0, 0.0))
        if until and self._now() >= until:
            n = 0
        self._fails[k] = (n + 1, self._now() + LOCKOUT_S)

    def check_login(self, ip: str, user: str, password: str) -> bool:
        if self.locked(ip, user):
            return False
        stored = self.users.get(user)
        ok = _verify_hash(password, stored or _DUMMY) and stored is not None
        if ok:
            self._fails.pop(self._key(ip, user), None)
        else:
            self._fail(ip, user)
        return ok

    # --- session ---
    def _sign(self, payload: bytes) -> str:
        return hmac.new(self.secret, payload, hashlib.sha256).hexdigest()

    def make_token(self, user: str) -> str:
        payload = base64.urlsafe_b64encode(
            json.dumps({"u": user, "exp": int(self._now()) + SESSION_TTL}).encode()
        )
        return f"{payload.decode()}.{self._sign(payload)}"

    def read_token(self, token: str | None) -> str | None:
        if not token or "." not in token:
            return None
        payload_s, sig = token.rsplit(".", 1)
        payload = payload_s.encode()
        if not hmac.compare_digest(self._sign(payload), sig):
            return None
        try:
            data = json.loads(base64.urlsafe_b64decode(payload))
        except ValueError:
            return None
        if data.get("exp", 0) < self._now() or data.get("u") not in self.users:
            return None
        return data["u"]
