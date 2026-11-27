#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
#
# Runs install.sh / uninstall.sh for real in a THROW-AWAY Ubuntu 24.04 container with systemd, on a
# Docker host, and writes a report. Nothing on the host is touched besides the container and image.
#
#     sudo tools/test-install-container.sh [SOURCE_DIR] [REPORT_DIR]
#
# SOURCE_DIR: the CodeCome Web tree to test (default: this script's repository).
# REPORT_DIR: where report.txt goes (default /var/tmp/ccw-install-test; world-readable, no secrets).
# Scenarios: postpone without CodeCome, fresh install, login over HTTPS, upgrade, uninstall keeping
# data, purge; then the same with everything under /home (ACL path).
set -uo pipefail

SRC="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
OUT="${2:-/var/tmp/ccw-install-test}"
IMG=ccw-install-test:ubuntu24.04
NAME=ccw-install-test
PASS=0; FAIL=0

[ "$(id -u)" -eq 0 ] || { echo "Run it with sudo." >&2; exit 1; }
[ -f "$SRC/install.sh" ] || { echo "No install.sh in $SRC" >&2; exit 1; }
install -d -m 0755 "$OUT"; REPORT="$OUT/report.txt"; : > "$REPORT"; chmod 0644 "$REPORT"
log()  { printf '%s\n' "$*" | tee -a "$REPORT"; }
check() {  # check "description" command...
  local d=$1; shift
  if "$@" >>"$REPORT" 2>&1; then PASS=$((PASS+1)); log "  PASS  $d"; else FAIL=$((FAIL+1)); log "  FAIL  $d"; fi
}
in_c() { docker exec "$NAME" bash -lc "$*"; }

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

log "== CodeCome Web install test — $(date -Is)"
log "source: $SRC ($(git -C "$SRC" rev-parse --short HEAD 2>/dev/null || echo 'not a git tree'))"

log "docker $(docker version --format '{{.Server.Version}}'), cgroup v$(docker info --format '{{.CgroupVersion}}') ($(docker info --format '{{.CgroupDriver}}'))"
log "== building the test image (Ubuntu 24.04 + systemd)"
docker build -q -t "$IMG" - >>"$REPORT" 2>&1 <<'EOF'
FROM ubuntu:24.04
RUN apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q systemd systemd-sysv iproute2 \
 && apt-get clean && rm -rf /var/lib/apt/lists/* \
 && systemctl mask systemd-logind.service getty.target console-getty.service
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
EOF
[ $? -eq 0 ] || { log "image build failed"; exit 1; }

CGROUP_MODE=""
start_container() {  # systemd in Docker, never --privileged. Tries the most isolated way first.
  local mode
  for mode in private hostns; do
    cleanup
    case $mode in
      private) docker run -d --name "$NAME" --hostname ccwtest --cgroupns=private --tmpfs /run --tmpfs /run/lock \
                 -e container=docker "$IMG" >>"$REPORT" 2>&1 ;;
      hostns)  docker run -d --name "$NAME" --hostname ccwtest --cgroupns=host --tmpfs /run --tmpfs /run/lock \
                 -v /sys/fs/cgroup:/sys/fs/cgroup:rw -e container=docker "$IMG" >>"$REPORT" 2>&1 ;;
    esac
    for _ in $(seq 1 30); do
      [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = true ] || break
      if in_c 'systemctl is-system-running 2>/dev/null | grep -qE "running|degraded"' 2>/dev/null; then
        CGROUP_MODE=$mode; log "   systemd up (cgroup mode: $mode)"; return 0
      fi
      sleep 1
    done
    log "   systemd did not start with cgroup mode '$mode'. Its log:"
    docker logs "$NAME" 2>&1 | tail -25 | sed 's/^/     | /' | tee -a "$REPORT" >/dev/null
  done
  return 1
}
copy_source() {  # copy_source DEST: the tree as a user would unpack it (no git, no venv, no data)
  in_c "mkdir -p $1"
  tar -C "$SRC" --exclude=.git --exclude=.venv --exclude=data --exclude=demo --exclude=__pycache__ -cf - . \
    | docker exec -i "$NAME" tar -C "$1" -xf -
}
fake_codecome() {  # fake_codecome DIR OWNER: the minimum the installer checks, like a real checkout
  in_c "mkdir -p $1/itemdb/findings/PENDING $1/src $1/runs $1/tools && touch $1/Makefile $1/src/.gitkeep \
    && printf 'project:\n  name: install-test\n' > $1/codecome.yml && printf '#!/bin/sh\nexit 0\n' > $1/tools/gate-check.py \
    && chown -R $2 $1"
}
web_ok() {  # the web answers over HTTPS with the installed CA, and the admin can log in
  in_c 'IP=$(ip -4 route get 1.1.1.1 | sed -n "s/.* src \([0-9.]*\).*/\1/p"); \
    curl -sf --cacert /etc/codecome-web/tls/ca.crt -o /dev/null https://$IP:8100/ \
    && curl -sf --cacert /etc/codecome-web/tls/ca.crt -c /tmp/j -H "Content-Type: application/json" \
         -d "{\"username\":\"admin\",\"password\":\"$(cat /root/pw)\"}" https://$IP:8100/api/login -o /dev/null \
    && curl -sf --cacert /etc/codecome-web/tls/ca.crt -b /tmp/j https://$IP:8100/api/me | grep -q admin'
}

# ------------------------------------------------------------------ scenario A: /opt
log "== A. container up"
check "systemd container starts" start_container || true
[ -n "$CGROUP_MODE" ] || { log "== ABORTED: systemd does not start in a container on this host (see its log above)"; exit 1; }
copy_source /root/ccw
in_c 'useradd -m dev; printf "a-long-test-password-123\n" > /root/pw; chmod 600 /root/pw'

log "== A1. postpone when CodeCome is missing"
check "exits 3 when CodeCome is missing" in_c '/root/ccw/install.sh --yes --skip-docker --codecome /opt/nowhere; test $? -eq 3'
check "…and changed nothing (no users, no units)" in_c '! id codecomeweb && ! test -e /etc/systemd/system/codecome-web.service'

log "== A2. fresh install"
fake_codecome /opt/CodeCome dev:dev
check "install.sh --yes completes" in_c '/root/ccw/install.sh --yes --skip-docker --admin admin --admin-password-file /root/pw'
check "both services active" in_c 'systemctl is-active codecome-web codecome-worker'
check "HTTPS answers and the admin logs in" web_ok
check "plain HTTP is refused" in_c 'IP=$(hostname -I | cut -d" " -f1); ! curl -sf -o /dev/null http://$IP:8100/'
check "users and shared group" in_c 'id -nG codecomeweb | grep -qw codecome-web && id -nG codecome | grep -qw codecome-web'
check "data 2770, archives 2750, server key 0640" in_c '[ "$(stat -c %a /opt/codecome-web/data)" = 2770 ] && [ "$(stat -c %a /opt/codecome-archives)" = 2750 ] && [ "$(stat -c %a /etc/codecome-web/tls/server.key)" = 640 ]'
check "CA key root-only" in_c '[ "$(stat -c "%a %U" /etc/codecome-web/tls/ca.key)" = "600 root" ]'
check "code not writable by the services" in_c '! runuser -u codecomeweb -- test -w /opt/codecome-web/app/main.py && ! runuser -u codecome -- test -w /opt/codecome-web/app'
check "worker can write CodeCome (ACL), web can read it" in_c 'runuser -u codecome -- test -w /opt/CodeCome/itemdb && runuser -u codecomeweb -- test -r /opt/CodeCome/codecome.yml'
check "worker.env 0600 owned by the worker" in_c '[ "$(stat -c "%a %U" /var/lib/codecome/worker.env)" = "600 codecome" ]'
check "password not in the manifest or units" in_c '! grep -rq "$(cat /root/pw)" /opt/codecome-web/.install-manifest /etc/systemd/system/codecome-*.service'

log "== A3. upgrade (re-run)"
check "re-run upgrades" in_c '/root/ccw/install.sh --yes --skip-docker'
check "still answers, same admin" web_ok
check "manifest keeps what the first run created" in_c 'grep -q "^CREATED_USERS=.*codecomeweb" /opt/codecome-web/.install-manifest'

log "== A4. uninstall (keep data)"
check "uninstall.sh --yes" in_c '/opt/codecome-web/uninstall.sh --yes'
check "services and code gone" in_c '! test -e /etc/systemd/system/codecome-web.service && ! test -e /opt/codecome-web/app'
check "data, archives, CA and users kept" in_c 'test -d /opt/codecome-web/data && test -d /opt/codecome-archives && test -f /etc/codecome-web/tls/ca.crt && id codecome'
check "CodeCome untouched" in_c 'test -f /opt/CodeCome/Makefile && test -f /opt/CodeCome/codecome.yml'

log "== A5. re-install over kept data, then purge"
check "re-install reuses the kept data" in_c '/root/ccw/install.sh --yes --skip-docker'
check "admin still logs in (users.json kept)" web_ok
check "uninstall.sh --purge --yes" in_c '/opt/codecome-web/uninstall.sh --purge --yes'
check "everything it created is gone" in_c '! id codecomeweb && ! id codecome && ! getent group codecome-web && ! test -e /opt/codecome-web && ! test -e /opt/codecome-archives && ! test -e /etc/codecome-web'
check "ACLs removed from CodeCome" in_c '! getfacl -p /opt/CodeCome 2>/dev/null | grep -q "user:codecome"'

# ------------------------------------------------------------------ scenario B: everything under /home
log "== B. everything under /home/dev (traverse ACLs, ProtectHome=read-only)"
check "fresh container" start_container || true
[ -n "$CGROUP_MODE" ] || { log "== ABORTED in scenario B"; exit 1; }
copy_source /root/ccw
in_c 'useradd -m dev; chmod 750 /home/dev; printf "a-long-test-password-123\n" > /root/pw; chmod 600 /root/pw'
fake_codecome /home/dev/CodeCome dev:dev
check "install under /home completes" in_c '/root/ccw/install.sh --yes --skip-docker --codecome /home/dev/CodeCome --prefix /home/dev/ccw --archives /home/dev/ccw-archives --admin admin --admin-password-file /root/pw'
check "HTTPS answers and the admin logs in" web_ok
check "traverse ACL on /home/dev, no chmod o+x" in_c 'getfacl -p /home/dev | grep -q "user:codecomeweb:--x" && [ "$(stat -c %a /home/dev)" = 750 ]'
check "units relax /home only to read-only" in_c 'grep -q "ProtectHome=read-only" /etc/systemd/system/codecome-web.service'
check "purge cleans the traverse ACLs" in_c '/home/dev/ccw/uninstall.sh --purge --yes && ! getfacl -p /home/dev | grep -q codecome'

log ""
log "== RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
