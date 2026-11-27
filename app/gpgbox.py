# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Symmetric encryption with the system's GnuPG (AES-256), no Python crypto dependency.

The passphrase goes to gpg through a pipe (`--passphrase-fd`), never argv, environment or files.
Each call uses a throw-away GNUPGHOME (0700) whose agent is killed and directory removed at the end,
and `--no-symkey-cache` so nothing is remembered. The output opens anywhere with `gpg -d`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

GPG = shutil.which("gpg") or "/usr/bin/gpg"
GPGCONF = shutil.which("gpgconf") or "/usr/bin/gpgconf"
COMMON = ["--batch", "--no-tty", "--quiet", "--pinentry-mode", "loopback", "--no-symkey-cache",
          "--no-options", "--no-permission-warning"]
ENCRYPT = ["--symmetric", "--cipher-algo", "AES256", "--s2k-mode", "3", "--s2k-digest-algo", "SHA512",
           "--s2k-count", "65011712", "--compress-algo", "none"]  # the tar is already gzip'ed
ERR_MAX = 4000


class GpgError(Exception):
    """code: gpg_missing | gpg_failed | bad_passphrase. `detail` (gpg's stderr) is for logs only."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


def available() -> bool:
    return os.access(GPG, os.X_OK)


@contextmanager
def _home() -> Iterator[str]:
    home = tempfile.mkdtemp(prefix="ccw-gnupg-")
    os.chmod(home, 0o700)
    try:
        yield home
    finally:
        try:
            subprocess.run([GPGCONF, "--homedir", home, "--kill", "gpg-agent"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10, env=_env(home))
        except (OSError, subprocess.SubprocessError):
            pass
        shutil.rmtree(home, ignore_errors=True)


def _env(home: str) -> dict:
    return {"PATH": "/usr/bin:/bin", "GNUPGHOME": home, "LANG": "C", "LC_ALL": "C"}


def _pass_pipe(passphrase: bytes | bytearray) -> int:
    """Read end of a pipe that already holds the passphrase (short: fits the pipe buffer)."""
    if not passphrase or b"\n" in passphrase or b"\r" in passphrase or b"\x00" in passphrase or len(passphrase) > 1024:
        raise GpgError("bad_passphrase")
    r, w = os.pipe()
    try:
        os.write(w, bytes(passphrase) + b"\n")
    finally:
        os.close(w)
    return r


class _Proc:
    """A running gpg with its stderr captured to an unnamed temp file (no pipe deadlock)."""

    def __init__(self, args: list[str], passphrase, *, stdin=None, stdout=None):
        if not available():
            raise GpgError("gpg_missing")
        self._home_cm = _home()
        home = self._home_cm.__enter__()
        self.err = tempfile.TemporaryFile()
        fd = _pass_pipe(passphrase)
        try:
            self.p = subprocess.Popen([GPG, "--homedir", home, *COMMON, "--passphrase-fd", str(fd), *args],
                                      stdin=stdin, stdout=stdout, stderr=self.err, pass_fds=(fd,), env=_env(home))
        except BaseException:
            self._home_cm.__exit__(None, None, None)
            raise
        finally:
            os.close(fd)

    def finish(self, bad_code: str = "gpg_failed") -> None:
        try:
            rc = self.p.wait()
            if rc != 0:
                self.err.seek(0)
                detail = self.err.read(ERR_MAX).decode("utf-8", "replace")
                bad = "bad session key" in detail.lower() or "decryption failed" in detail.lower()
                raise GpgError("bad_passphrase" if bad else bad_code, detail)
        finally:
            self.err.close()
            self._home_cm.__exit__(None, None, None)

    def kill(self) -> None:
        try:
            self.p.kill()
            self.p.wait()
        finally:
            self.err.close()
            self._home_cm.__exit__(None, None, None)


@contextmanager
def encrypt_to(out: Path, passphrase) -> Iterator[BinaryIO]:
    """`with encrypt_to(path, pw) as w: w.write(...)`: what is written ends up encrypted in `out`
    (0640). On any error the partial output is removed."""
    out = Path(out)
    proc = _Proc([*ENCRYPT, "--output", str(out), "--yes"], passphrase, stdin=subprocess.PIPE,
                 stdout=subprocess.DEVNULL)
    try:
        yield proc.p.stdin
        proc.p.stdin.close()
        proc.finish()
        os.chmod(out, 0o640)
    except BaseException:
        try:
            proc.p.stdin.close()
        except OSError:
            pass
        proc.kill()
        out.unlink(missing_ok=True)
        raise


@contextmanager
def decrypt_from(src: Path, passphrase) -> Iterator[BinaryIO]:
    """`with decrypt_from(path, pw) as r: r.read(...)`: the plaintext as a stream (never on disk).
    A wrong passphrase raises GpgError('bad_passphrase') when the block exits (or earlier as EOF)."""
    proc = _Proc(["--decrypt", str(src)], passphrase, stdout=subprocess.PIPE)
    try:
        yield proc.p.stdout
        proc.p.stdout.read()  # drain: gpg verifies the MDC only at the end
        proc.p.stdout.close()
        proc.finish()
    except GpgError:
        raise
    except BaseException:
        proc.p.stdout.close()
        try:
            proc.finish()  # a wrong passphrase surfaces as a broken stream: report the real cause
        except GpgError as e:
            if e.code in ("bad_passphrase", "gpg_missing"):
                raise e from None
            # otherwise gpg only died because we stopped reading: the caller's error is the real one
        raise
