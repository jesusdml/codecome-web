#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
#
# Removes CodeCome Web, undoing exactly what install.sh recorded in its manifest.
#
#     sudo PREFIX/uninstall.sh            stop and remove the services and the code;
#                                         KEEPS data, archives, the TLS CA and the users
#     sudo PREFIX/uninstall.sh --purge    also deletes data, archives (encrypted audits!), the CA,
#                                         the users/groups it created, its group memberships and ACLs
#     --yes                               do not ask
#
# Never touches CodeCome itself (only the ACLs the installer added on it) and never removes system
# packages (they may be used by something else: the list is shown).
set -euo pipefail

PURGE=0; YES=0
for a in "$@"; do
  case "$a" in --purge) PURGE=1 ;; --yes|-y) YES=1 ;; *) echo "Unknown option: $a" >&2; exit 2 ;; esac
done
[ "$(id -u)" -eq 0 ] || { echo "Run it with sudo." >&2; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="$HERE/.install-manifest"
[ -f "$MANIFEST" ] || { echo "No installation manifest in $HERE (is this the install folder?)." >&2; exit 1; }

declare -A M=()
while IFS='=' read -r k v; do [[ "$k" =~ ^[A-Z_]+$ ]] && M[$k]=$v; done < "$MANIFEST"
PREFIX=${M[PREFIX]}; DATA=${M[DATA]}; ARCHIVES=${M[ARCHIVES]}; TLS_DIR=${M[TLS_DIR]}
[ "$PREFIX" = "$HERE" ] || { echo "Manifest PREFIX ($PREFIX) is not this folder ($HERE)." >&2; exit 1; }
for p in "$PREFIX" "$DATA" "$ARCHIVES" "$TLS_DIR"; do
  [[ "$p" =~ ^/[A-Za-z0-9._/-]+$ ]] && [ "$p" != / ] && [[ "$p" != *..* ]] || { echo "Refusing odd path in manifest: '$p'" >&2; exit 1; }
done

confirm() { [ "$YES" = 1 ] && return 0; local a; read -r -p "$1 [y/N]: " a </dev/tty || true; [[ "$a" =~ ^[YySs] ]]; }

echo "This removes CodeCome Web from $PREFIX."
[ "$PURGE" = 1 ] && echo "PURGE: it also deletes $DATA, $ARCHIVES (encrypted audits) and $TLS_DIR, and the users/groups it created."
confirm "Continue?" || { echo "Nothing changed."; exit 0; }
if [ "$PURGE" = 1 ] && [ -d "$ARCHIVES" ] && [ -n "$(ls -A "$ARCHIVES" 2>/dev/null)" ]; then
  confirm "$ARCHIVES holds $(ls "$ARCHIVES"/*.gpg 2>/dev/null | wc -l) archived audit(s) that cannot be recovered afterwards. Delete them?" \
    || { echo "Stopped. Copy the archives elsewhere first, or run without --purge."; exit 0; }
fi

echo "== services"
for s in codecome-web codecome-worker; do
  systemctl disable --now "$s" 2>/dev/null || true
  rm -f "/etc/systemd/system/$s.service"
done
systemctl daemon-reload

echo "== code"
rm -rf "$PREFIX/app" "$PREFIX/tools" "$PREFIX/.venv" "$PREFIX/deploy" "$PREFIX/config"
rm -f "$PREFIX"/{worker.py,requirements.txt,pyproject.toml,LICENSE,AGPL-LICENSE,NOTICE}

if [ "$PURGE" = 1 ]; then
  echo "== data, archives, CA"
  rm -rf "$DATA" "$ARCHIVES" "$TLS_DIR"
  rmdir "$(dirname "$TLS_DIR")" 2>/dev/null || true
  echo "== ACLs added by the installer"
  for e in ${M[ACLS]-}; do
    IFS='|' read -r path who rec <<<"$e"
    if [ -e "$path" ]; then
      if [ "$rec" = R ]; then setfacl -R -x "$who" "$path" 2>/dev/null || true; setfacl -R -d -x "$who" "$path" 2>/dev/null || true
      else setfacl -x "$who" "$path" 2>/dev/null || true; fi
    fi
  done
  echo "== group memberships, users and groups created by the installer"
  for e in ${M[ADDED_TO_GROUPS]-}; do gpasswd -d "${e%%:*}" "${e##*:}" >/dev/null 2>&1 || true; done
  for u in ${M[CREATED_USERS]-}; do userdel -r "$u" 2>/dev/null || userdel "$u" 2>/dev/null || true; done
  for g in ${M[CREATED_GROUPS]-}; do groupdel "$g" 2>/dev/null || true; done
  rm -f "$MANIFEST" "$MANIFEST.new" "$PREFIX/uninstall.sh"
  rmdir "$PREFIX" 2>/dev/null || echo "   $PREFIX is not empty: left in place."
else
  echo "Kept: $DATA, $ARCHIVES, $TLS_DIR, the users and the manifest (re-installing reuses them)."
fi
[ -n "${M[INSTALLED_PKGS]-}" ] && echo "System packages installed back then (not removed): ${M[INSTALLED_PKGS]}"
echo "Done."
