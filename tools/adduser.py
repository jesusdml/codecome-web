#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Creates or updates a user. The password is typed at the keyboard (never through chat).

Usage:  .venv/bin/python tools/adduser.py NAME                    (asks twice, no echo)
        .venv/bin/python tools/adduser.py --password-stdin NAME   (one line on stdin: installers)
The users file is $CODECOME_USERS (default data/users.json); only a scrypt hash is stored.
"""
import getpass
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.auth import hash_password  # noqa: E402

args = sys.argv[1:]
from_stdin = "--password-stdin" in args
args = [a for a in args if a != "--password-stdin"]
if len(args) != 1 or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", args[0]):
    sys.exit("usage: adduser.py [--password-stdin] NAME   (letters, digits, _ . -)")
name = args[0]

base = Path(__file__).resolve().parent.parent
path = Path(os.environ.get("CODECOME_USERS", base / "data" / "users.json"))
path.parent.mkdir(parents=True, exist_ok=True)
if from_stdin:
    pw = sys.stdin.readline().rstrip("\n")
else:
    pw = getpass.getpass("Password: ")
if len(pw) < 12:
    sys.exit("At least 12 characters.")
if not from_stdin and pw != getpass.getpass("Repeat it: "):
    sys.exit("They do not match.")
users = json.loads(path.read_text()) if path.exists() else {}
users[name] = hash_password(pw)
tmp = path.with_name("." + path.name + ".tmp")
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, "w") as f:
    os.fchmod(f.fileno(), 0o600)
    json.dump(users, f, indent=2)
os.replace(tmp, path)
print(f"User {name} saved in {path}")
