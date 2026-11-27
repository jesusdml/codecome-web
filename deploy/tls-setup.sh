#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
# HTTPS for CodeCome Web with a small LOCAL certificate authority ("CodeCome LAN CA").
#
# Usage:  sudo tls-setup.sh [--renew] [--dir DIR] [--group GROUP] [--ip IP] [--names "n1 n2"]
#   --renew   new server certificate signed by the existing CA (e.g. before it expires)
#   --dir     where the CA and the certificate live      (default /etc/codecome-web/tls)
#   --group   group allowed to read the server key       (default codecomeweb)
#   --ip      address the certificate is valid for       (default: this host's main IPv4)
#   --names   DNS names it is valid for                  (default: short and full host name)
# Creates only what is missing (idempotent). Called by install.sh.
#
# - The CA is NAME-CONSTRAINED to exactly that IP and those names: even installed in a browser it
#   cannot vouch for any other site. Its private key stays in DIR, root only (0600).
# - The server key is readable by GROUP only (0640 root:GROUP).
# - Install ca.crt (public) once in each browser / device: then the padlock shows with no warning.
set -euo pipefail

DIR=/etc/codecome-web/tls
GROUP=codecomeweb
IP=""
NAMES=""
RENEW=0
CA_DAYS=3650
SRV_DAYS=825   # browsers refuse longer server certificates

while [ $# -gt 0 ]; do
  case "$1" in
    --renew) RENEW=1 ;;
    --dir) DIR="$2"; shift ;;
    --group) GROUP="$2"; shift ;;
    --ip) IP="$2"; shift ;;
    --names) NAMES="$2"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

[ "$(id -u)" -eq 0 ] || { echo "Run it with sudo." >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl is missing." >&2; exit 1; }
getent group "$GROUP" >/dev/null || { echo "Group $GROUP does not exist." >&2; exit 1; }
if [ -z "$IP" ]; then
  IP=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)
fi
[[ "$IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "Could not tell this host's IPv4; pass --ip." >&2; exit 1; }
if [ -z "$NAMES" ]; then
  NAMES=$(printf '%s\n' "$(hostname -s)" "$(hostname -f 2>/dev/null || true)" | grep -E '^[A-Za-z0-9.-]+$' | sort -u | tr '\n' ' ')
fi
for n in $NAMES; do
  [[ "$n" =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*$ ]] || { echo "Bad DNS name: $n" >&2; exit 1; }
done

umask 077
install -d -o root -g root -m 0755 "$(dirname "$DIR")"
install -d -o root -g "$GROUP" -m 0750 "$DIR"
cd "$DIR"

if [ ! -s ca.key ] || [ ! -s ca.crt ]; then
  echo "== creating the local CA (valid $CA_DAYS days)"
  NC="permitted;IP:$IP/255.255.255.255"
  for n in $NAMES; do NC="$NC,permitted;DNS:$n"; done
  openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out ca.key
  openssl req -x509 -new -key ca.key -sha256 -days "$CA_DAYS" -subj "/CN=CodeCome LAN CA/O=CodeCome Web" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "nameConstraints=critical,$NC" \
    -out ca.crt
  chmod 0600 ca.key; chmod 0644 ca.crt
  rm -f server.crt   # a new CA needs a new server certificate
fi

if [ ! -s server.crt ] || [ ! -s server.key ] || [ "$RENEW" = 1 ]; then
  echo "== creating the server certificate (valid $SRV_DAYS days): IP $IP, names: $NAMES"
  SAN="IP:$IP"; for n in $NAMES; do SAN="$SAN,DNS:$n"; done
  CN=$(echo "$NAMES" | awk '{print $NF}'); CN=${CN:-$IP}
  openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out server.key.new
  openssl req -new -key server.key.new -subj "/CN=$CN/O=CodeCome Web" -out server.csr
  printf 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nsubjectAltName=%s\n' \
    "$SAN" > server.ext
  openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -sha256 -days "$SRV_DAYS" \
    -extfile server.ext -out server.crt.new
  rm -f server.csr server.ext
  mv server.key.new server.key; mv server.crt.new server.crt
  chown root:"$GROUP" server.key server.crt; chmod 0640 server.key; chmod 0644 server.crt
fi

openssl verify -CAfile ca.crt server.crt >/dev/null && echo "   certificate OK (signed by the local CA)"
echo "   expires: $(openssl x509 -in server.crt -noout -enddate | cut -d= -f2)"
# The CA certificate is public: the web also serves it at /ca.crt for installing it in browsers.
echo "   public CA: $DIR/ca.crt"
echo "   CA fingerprint SHA-256: $(openssl x509 -in ca.crt -noout -fingerprint -sha256 | cut -d= -f2)"
echo "   CA fingerprint SHA-1 (what Windows shows): $(openssl x509 -in ca.crt -noout -fingerprint -sha1 | cut -d= -f2)"
