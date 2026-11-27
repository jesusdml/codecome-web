#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
#
# CodeCome Web installer for Debian / Ubuntu (systemd). Run it from the unpacked source tree:
#
#     sudo ./install.sh                 interactive (recommended the first time)
#     sudo ./install.sh --yes [opts]    non-interactive: defaults + the options below
#
# It does everything, nothing to do outside it: system packages, service users and group,
# directories and permissions, the Python virtualenv, systemd units, HTTPS with a local CA,
# the first admin account; then it starts the services and checks they answer.
# Re-running it upgrades an existing installation (the answers are kept in its manifest).
# CodeCome itself is NOT installed by this script: it must already be there (asked first).
#
# Options (all optional; interactive mode asks for anything not given):
#   --codecome DIR        where CodeCome is                       (default /opt/CodeCome)
#   --prefix DIR          where CodeCome Web goes                 (default /opt/codecome-web)
#   --data DIR            runtime data                            (default PREFIX/data)
#   --archives DIR        encrypted archives                      (default /opt/codecome-archives)
#   --web-user NAME       user of the web service                 (default codecomeweb)
#   --worker-user NAME    user of the worker (runs phases, Docker) (default codecome)
#   --group NAME          group shared by both                    (default codecome-web)
#   --ip IP               address the HTTPS certificate is for    (default: this host's main IPv4)
#   --names "n1 n2"       DNS names for the certificate           (default: host name)
#   --host ADDR           address the web listens on              (default: same as --ip)
#   --port N              HTTPS port                              (default 8100)
#   --admin NAME          first admin web user (password: asked, or one line on stdin with --yes)
#   --admin-password-file F   read that password from F (first line; keep it 0600, delete it after)
#   --skip-docker         do not check or offer Docker (e.g. it is managed elsewhere)
#   --yes                 do not ask; accept defaults and the warnings shown
set -euo pipefail
umask 022

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TLS_DIR=/etc/codecome-web/tls
UNIT_DIR=/etc/systemd/system
PKGS=(python3 python3-venv gpg openssl rsync acl ca-certificates curl iproute2)
YES=0
SKIP_DOCKER=0
declare -A OPT=()
CREATED_USERS=() CREATED_GROUPS=() ADDED_TO_GROUPS=() ACLS=() INSTALLED_PKGS=()

# ---------------------------------------------------------------- helpers
say()  { printf '%s\n' "$*"; }
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
warn() { printf '\033[33m!! %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31mxx %s\033[0m\n' "$*" >&2; exit 1; }

ask() {  # ask VAR "question" "default"
  local __var=$1 q=$2 def=${3-} ans
  if [ -n "${OPT[$__var]-}" ]; then printf -v "$__var" '%s' "${OPT[$__var]}"; return; fi
  if [ "$YES" = 1 ]; then printf -v "$__var" '%s' "$def"; return; fi
  read -r -p "$q [${def}]: " ans </dev/tty || true
  printf -v "$__var" '%s' "${ans:-$def}"
}

confirm() {  # confirm "question" [default y|n]  -> 0 yes, 1 no
  local q=$1 def=${2:-y} ans
  if [ "$YES" = 1 ]; then return 0; fi  # --yes accepts the defaults AND the warnings shown
  read -r -p "$q [$([ "$def" = y ] && echo Y/n || echo y/N)]: " ans </dev/tty || true
  ans=${ans:-$def}
  [[ "$ans" =~ ^[YySs] ]]
}

safe_path() {  # absolute, no spaces or shell/sed specials: these end up in unit files
  [[ "$1" =~ ^/[A-Za-z0-9._/-]*$ ]] && [[ "$1" != *..* ]] || die "Unsupported path: '$1' (letters, digits, . _ - / only)"
}
safe_name() { [[ "$1" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "Unsupported user/group name: '$1'"; }

as_user() { runuser -u "$1" -- "${@:2}"; }

under_home() { case "$1" in /home/*|/root/*) return 0 ;; *) return 1 ;; esac; }

# ---------------------------------------------------------------- options
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1 ;;
    --codecome) OPT[CODECOME]=$2; shift ;;
    --prefix) OPT[PREFIX]=$2; shift ;;
    --data) OPT[DATA]=$2; shift ;;
    --archives) OPT[ARCHIVES]=$2; shift ;;
    --web-user) OPT[WEB_USER]=$2; shift ;;
    --worker-user) OPT[WORKER_USER]=$2; shift ;;
    --group) OPT[GROUP]=$2; shift ;;
    --ip) OPT[IP]=$2; shift ;;
    --names) OPT[NAMES]=$2; shift ;;
    --host) OPT[HOST]=$2; shift ;;
    --port) OPT[PORT]=$2; shift ;;
    --admin) OPT[ADMIN]=$2; shift ;;
    --admin-password-file) OPT[ADMIN_PW_FILE]=$2; shift ;;
    --skip-docker) SKIP_DOCKER=1 ;;
    -h|--help) sed -n '5,32s/^# \{0,1\}//p' "$0"; exit 0 ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
  shift
done

[ "$(id -u)" -eq 0 ] || die "Run it with sudo: sudo $0"
[ -f "$SRC/app/main.py" ] && [ -f "$SRC/worker.py" ] || die "Run it from the CodeCome Web source tree."
. /etc/os-release 2>/dev/null || true
[[ " ${ID:-} ${ID_LIKE:-} " == *" debian "* || "${ID:-}" == debian || "${ID:-}" == ubuntu ]] \
  || die "This installer supports Debian and Ubuntu (found: ${PRETTY_NAME:-unknown})."
command -v systemctl >/dev/null || die "systemd is required."

say "CodeCome Web installer — $(sed -n 's/^version = "\(.*\)"/\1/p' "$SRC/pyproject.toml")"

# ---------------------------------------------------------------- 1. CodeCome first
step "1/9 CodeCome"
ask CODECOME "Where is CodeCome installed?" "${OPT[CODECOME]:-/opt/CodeCome}"
CODECOME=${CODECOME%/}
safe_path "$CODECOME"
if [ ! -d "$CODECOME" ] || [ ! -f "$CODECOME/Makefile" ] || [ ! -d "$CODECOME/itemdb" ]; then
  warn "CodeCome was not found in $CODECOME (it needs at least a Makefile and an itemdb/ folder)."
  say  "CodeCome Web is an interface for CodeCome, so CodeCome must be installed first."
  say  "Install CodeCome first (https://github.com/pruiz/CodeCome), then run this installer again."
  say  "Nothing has been changed."
  exit 3
fi
for f in codecome.yml tools/gate-check.py src; do
  [ -e "$CODECOME/$f" ] || { warn "$CODECOME/$f is missing (an adapted CodeCome?)."; confirm "Use this CodeCome anyway?" y || exit 3; }
done
say "CodeCome found in $CODECOME"

# ---------------------------------------------------------------- 2. where we go (and upgrade?)
step "2/9 Where CodeCome Web goes"
ask PREFIX "Install CodeCome Web in" "${OPT[PREFIX]:-/opt/codecome-web}"
PREFIX=${PREFIX%/}; safe_path "$PREFIX"
MANIFEST="$PREFIX/.install-manifest"
if [ -f "$MANIFEST" ]; then
  say "An existing installation was found: it will be UPGRADED with its previous answers."
  while IFS='=' read -r k v; do
    case "$k" in DATA|ARCHIVES|WEB_USER|WORKER_USER|GROUP|IP|NAMES|HOST|PORT|ADMINS)
      [ -n "${OPT[$k]-}" ] || OPT[$k]=$v ;; esac
  done < "$MANIFEST"
  UPGRADE=1
else
  UPGRADE=0
fi
ask DATA "Runtime data in" "$PREFIX/data";              DATA=${DATA%/}; safe_path "$DATA"
ask ARCHIVES "Encrypted archives in" "/opt/codecome-archives"; ARCHIVES=${ARCHIVES%/}; safe_path "$ARCHIVES"
for p in "$DATA" "$ARCHIVES"; do
  case "$p/" in "$CODECOME"/*) die "$p must not be inside CodeCome's folder." ;; esac
done

# ---------------------------------------------------------------- 3. users
step "3/9 Service users"
say "Two users are recommended: the web (no privileges) and the worker (runs the phases, uses Docker)."
ask WEB_USER "User for the web service" "codecomeweb";    safe_name "$WEB_USER"
ask WORKER_USER "User for the worker" "codecome";         safe_name "$WORKER_USER"
ask GROUP "Group shared by both" "codecome-web";          safe_name "$GROUP"
if [ "$WEB_USER" = "$WORKER_USER" ]; then
  warn "One user for both services loses the separation: a compromised web would hold the API keys and Docker."
  confirm "I understand; continue with a single user?" n || die "Choose two different users."
fi
if id "$WORKER_USER" >/dev/null 2>&1 && [ "$(id -u "$WORKER_USER")" -ge 1000 ]; then
  warn "$WORKER_USER is a personal account: joining the 'docker' group makes it equivalent to root on this host."
  confirm "Continue with $WORKER_USER as the worker?" n || die "Choose another worker user."
fi

# ---------------------------------------------------------------- 4. packages
step "4/9 System packages"
MISSING=()
for p in "${PKGS[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || MISSING+=("$p"); done
if [ ${#MISSING[@]} -gt 0 ]; then
  say "Missing: ${MISSING[*]}"
  confirm "Install them with apt?" y || die "These packages are required."
  apt-get update -q
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${MISSING[@]}"
  INSTALLED_PKGS+=("${MISSING[@]}")
else
  say "All present: ${PKGS[*]}"
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' || die "Python 3.12 or newer is required ($(python3 --version))."
if [ "$SKIP_DOCKER" = 0 ] && ! command -v docker >/dev/null; then
  warn "Docker is not installed. CodeCome runs its sandboxes with it."
  if confirm "Install Docker from the distribution (docker.io)?" y; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q docker.io; INSTALLED_PKGS+=(docker.io)
  else
    warn "Continuing without Docker: phases that need a sandbox will fail until it is installed."
  fi
fi

# ---------------------------------------------------------------- 5. users, group, permissions
step "5/9 Users, group and permissions"
if ! getent group "$GROUP" >/dev/null; then groupadd --system "$GROUP"; CREATED_GROUPS+=("$GROUP"); say "group $GROUP created"; fi
for u in "$WEB_USER" "$WORKER_USER"; do
  if ! id "$u" >/dev/null 2>&1; then
    useradd --system --user-group --create-home --home-dir "/var/lib/$u" --shell /usr/sbin/nologin "$u"
    CREATED_USERS+=("$u"); say "user $u created (home /var/lib/$u)"
  fi
  if ! id -nG "$u" | tr ' ' '\n' | grep -qx "$GROUP"; then usermod -aG "$GROUP" "$u"; ADDED_TO_GROUPS+=("$u:$GROUP"); fi
done
WORKER_HOME=$(getent passwd "$WORKER_USER" | cut -d: -f6)
WEB_PRIMARY=$(id -gn "$WEB_USER")
if command -v docker >/dev/null && getent group docker >/dev/null && ! id -nG "$WORKER_USER" | tr ' ' '\n' | grep -qx docker; then
  if confirm "Add $WORKER_USER to the 'docker' group (needed to run CodeCome's sandboxes)?" y; then
    usermod -aG docker "$WORKER_USER"; ADDED_TO_GROUPS+=("$WORKER_USER:docker")
  fi
fi

grant_traverse() {  # grant_traverse USER PATH: +x on every parent the user cannot cross (ACL, not chmod)
  local u=$1 d; d=$(dirname "$2")
  while [ "$d" != / ]; do
    if [ -d "$d" ] && ! as_user "$u" test -x "$d"; then
      if confirm "$u cannot enter $d. Grant it with an ACL (setfacl -m u:$u:x)?" y; then
        setfacl -m "u:$u:x" "$d"; ACLS+=("$d|u:$u")
      else die "$u needs to reach $2."; fi
    fi
    d=$(dirname "$d")
  done
}
for p in "$PREFIX" "$DATA" "$ARCHIVES" "$CODECOME"; do
  grant_traverse "$WEB_USER" "$p/x"; grant_traverse "$WORKER_USER" "$p/x"
done
# CodeCome's workspace: the worker writes there, the web reads it.
if ! as_user "$WORKER_USER" test -w "$CODECOME" -a -w "$CODECOME/itemdb"; then
  if confirm "$WORKER_USER cannot write in $CODECOME. Grant read/write with ACLs (recursive, also for new files)?" y; then
    setfacl -R -m "u:$WORKER_USER:rwX" "$CODECOME"; setfacl -R -d -m "u:$WORKER_USER:rwX" "$CODECOME"
    ACLS+=("$CODECOME|u:$WORKER_USER|R")
  else warn "The worker will not be able to run phases until it can write in $CODECOME."; fi
fi
if ! as_user "$WEB_USER" test -r "$CODECOME/itemdb" -a -x "$CODECOME/itemdb"; then
  if confirm "$WEB_USER cannot read $CODECOME. Grant read-only with ACLs (recursive, also for new files)?" y; then
    setfacl -R -m "u:$WEB_USER:rX" "$CODECOME"; setfacl -R -d -m "u:$WEB_USER:rX" "$CODECOME"
    ACLS+=("$CODECOME|u:$WEB_USER|R")
  else warn "The web will not be able to show findings until it can read $CODECOME."; fi
fi

# ---------------------------------------------------------------- 6. code, venv, config, dirs
step "6/9 Code, Python environment and directories"
if [ "$UPGRADE" = 1 ]; then systemctl stop codecome-web codecome-worker 2>/dev/null || true; fi
install -d -o root -g root -m 0755 "$PREFIX" "$PREFIX/config" "$PREFIX/deploy"
rsync -a --delete --exclude __pycache__ "$SRC/app" "$SRC/tools" "$PREFIX/"
install -o root -g root -m 0644 "$SRC/worker.py" "$SRC/requirements.txt" "$SRC/pyproject.toml" \
  "$SRC/LICENSE" "$SRC/AGPL-LICENSE" "$SRC/NOTICE" "$PREFIX/"
install -o root -g root -m 0755 "$SRC/deploy/tls-setup.sh" "$PREFIX/deploy/tls-setup.sh"
install -o root -g root -m 0755 "$SRC/uninstall.sh" "$PREFIX/uninstall.sh"
rsync -a --delete "$SRC/deploy/templates" "$PREFIX/deploy/"
[ -e "$PREFIX/config/profiles.json" ] || install -m 0644 "$SRC/config/profiles.example.json" "$PREFIX/config/profiles.json"
install -m 0644 "$SRC/config/prices.json" "$PREFIX/config/prices.json"   # ours: refreshed on upgrade
[ -d "$SRC/config/mock" ] && rsync -a "$SRC/config/mock" "$PREFIX/config/"
chown -R root:root "$PREFIX/app" "$PREFIX/tools" "$PREFIX/config"; chmod -R go-w "$PREFIX/app" "$PREFIX/tools"
[ -x "$PREFIX/.venv/bin/python" ] || python3 -m venv "$PREFIX/.venv"
"$PREFIX/.venv/bin/pip" install -q --disable-pip-version-check -r "$PREFIX/requirements.txt"
# Data: web and worker share it through GROUP (setgid keeps the group on new files).
install -d -o "$WEB_USER" -g "$GROUP" -m 2770 "$DATA" "$DATA/logs" "$DATA/secrets" "$DATA/uploads"
install -d -o "$WORKER_USER" -g "$GROUP" -m 2750 "$ARCHIVES"
[ -e "$WORKER_HOME/worker.env" ] || install -o "$WORKER_USER" -g "$(id -gn "$WORKER_USER")" -m 0600 /dev/null "$WORKER_HOME/worker.env"
say "code in $PREFIX, data in $DATA, archives in $ARCHIVES"

# ---------------------------------------------------------------- 7. HTTPS
step "7/9 HTTPS (local CA)"
DEF_IP=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)
DEF_NAMES=$(printf '%s\n' "$(hostname -s)" "$(hostname -f 2>/dev/null || true)" | grep -E '^[A-Za-z0-9.-]+$' | sort -u | tr '\n' ' ' | sed 's/ $//')
ask IP "IP address for the certificate" "$DEF_IP"
ask NAMES "DNS names for the certificate (space separated)" "$DEF_NAMES"
ask HOST "Address the web listens on" "$IP"
ask PORT "HTTPS port" "8100"
[[ "$PORT" =~ ^[0-9]{2,5}$ ]] && [ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] || die "Port must be 1024-65535 (no extra privileges)."
[[ "$HOST" =~ ^[0-9.]+$|^::?$|^[0-9a-fA-F:]+$ ]] || die "Listen address must be an IP (e.g. $IP or 0.0.0.0)."
"$PREFIX/deploy/tls-setup.sh" --dir "$TLS_DIR" --group "$WEB_PRIMARY" --ip "$IP" --names "$NAMES"

# ---------------------------------------------------------------- 8. first admin + units
step "8/9 First administrator and services"
ADMINS=${OPT[ADMINS]-}
USERS_FILE="$DATA/users.json"
if [ -n "${OPT[ADMIN]-}" ] || [ ! -s "$USERS_FILE" ]; then
  ask ADMIN "Name of the first admin web user" "${SUDO_USER:-admin}"
  [[ "$ADMIN" =~ ^[A-Za-z0-9_.-]{1,64}$ ]] || die "Admin name: letters, digits, _ . - only."
  if [ -n "${OPT[ADMIN_PW_FILE]-}" ]; then
    [ -r "${OPT[ADMIN_PW_FILE]}" ] || die "Cannot read ${OPT[ADMIN_PW_FILE]}"
    as_user "$WEB_USER" env CODECOME_USERS="$USERS_FILE" "$PREFIX/.venv/bin/python" "$PREFIX/tools/adduser.py" \
      --password-stdin "$ADMIN" < "${OPT[ADMIN_PW_FILE]}"
  elif [ "$YES" = 1 ]; then
    as_user "$WEB_USER" env CODECOME_USERS="$USERS_FILE" "$PREFIX/.venv/bin/python" "$PREFIX/tools/adduser.py" --password-stdin "$ADMIN"
  else
    say "Password for $ADMIN (at least 12 characters; not shown while typing):"
    as_user "$WEB_USER" env CODECOME_USERS="$USERS_FILE" "$PREFIX/.venv/bin/python" "$PREFIX/tools/adduser.py" "$ADMIN" </dev/tty
  fi
  case ",$ADMINS," in *",$ADMIN,"*) ;; *) ADMINS=${ADMINS:+$ADMINS,}$ADMIN ;; esac
fi

PROTECT=true; WORKER_RW=""
for p in "$PREFIX" "$DATA" "$ARCHIVES" "$CODECOME" "$WORKER_HOME"; do
  if under_home "$p"; then PROTECT=read-only; fi
done
if [ "$PROTECT" = read-only ]; then  # only what the worker writes; ProtectSystem=full already allows /opt
  WORKER_RW="ReadWritePaths=$CODECOME $DATA $ARCHIVES $WORKER_HOME"
  say "Some paths are under /home: /home is read-only for the services except those paths."
fi
render() {  # render TEMPLATE OUT
  sed -e "s|@WEB_USER@|$WEB_USER|g" -e "s|@WORKER_USER@|$WORKER_USER|g" -e "s|@GROUP@|$GROUP|g" \
      -e "s|@PREFIX@|$PREFIX|g" -e "s|@DATA@|$DATA|g" -e "s|@ARCHIVES@|$ARCHIVES|g" -e "s|@CODECOME@|$CODECOME|g" \
      -e "s|@HOST@|$HOST|g" -e "s|@PORT@|$PORT|g" -e "s|@TLS_DIR@|$TLS_DIR|g" -e "s|@ADMINS@|$ADMINS|g" \
      -e "s|@WEB_GROUP@|$WEB_PRIMARY|g" -e "s|@WORKER_GROUP@|$(id -gn "$WORKER_USER")|g" \
      -e "s|@WORKER_HOME@|$WORKER_HOME|g" -e "s|@PROTECT_HOME@|$PROTECT|g" -e "s|@WORKER_RW@|$WORKER_RW|g" "$1" > "$2"
  ! grep -q '@[A-Z_]*@' "$2" || die "Unfilled placeholder in $2"
  chmod 0644 "$2"
}
render "$PREFIX/deploy/templates/codecome-web.service.in" "$UNIT_DIR/codecome-web.service"
render "$PREFIX/deploy/templates/codecome-worker.service.in" "$UNIT_DIR/codecome-worker.service"
systemctl daemon-reload
systemctl enable --now codecome-worker codecome-web >/dev/null

# What earlier runs created is kept, so uninstall can undo all of it. Plain bash: values contain '|'.
merged() {  # merged KEY current-items... -> previous + current, unique, space separated
  local k=$1 prev=""; shift
  [ -f "$MANIFEST" ] && prev=$(grep -m1 "^$k=" "$MANIFEST" | cut -d= -f2-)
  printf '%s\n' $prev "$@" | sed '/^$/d' | sort -u | tr '\n' ' ' | sed 's/ $//'
}
{ echo "# CodeCome Web installation manifest: written by install.sh, read by install.sh (upgrade) and uninstall.sh"
  echo "VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$SRC/pyproject.toml")"
  echo "INSTALLED_AT=$(date -Is)"
  for k in CODECOME PREFIX DATA ARCHIVES WEB_USER WORKER_USER GROUP IP NAMES HOST PORT ADMINS TLS_DIR WORKER_HOME; do echo "$k=${!k}"; done
  echo "CREATED_USERS=$(merged CREATED_USERS "${CREATED_USERS[@]}")"
  echo "CREATED_GROUPS=$(merged CREATED_GROUPS "${CREATED_GROUPS[@]}")"
  echo "ADDED_TO_GROUPS=$(merged ADDED_TO_GROUPS "${ADDED_TO_GROUPS[@]}")"
  echo "ACLS=$(merged ACLS "${ACLS[@]}")"
  echo "INSTALLED_PKGS=$(merged INSTALLED_PKGS "${INSTALLED_PKGS[@]}")"
} > "$MANIFEST.new"
mv "$MANIFEST.new" "$MANIFEST"; chmod 0644 "$MANIFEST"

# ---------------------------------------------------------------- 9. check
step "9/9 Checking"
URL="https://$IP:$PORT/"
ok=0
for _ in $(seq 1 20); do
  if [ "$(curl -s -o /dev/null -w '%{http_code}' --cacert "$TLS_DIR/ca.crt" "$URL" || true)" = 200 ]; then ok=1; break; fi
  sleep 1
done
for s in codecome-worker codecome-web; do printf '  %-16s %s\n' "$s" "$(systemctl is-active "$s")"; done
[ "$ok" = 1 ] || die "The web does not answer at $URL. See: journalctl -u codecome-web -n 50"

say ""
say "CodeCome Web is running:  $URL"
for n in $NAMES; do say "                          https://$n:$PORT/"; done
say ""
say "Trust the local CA once in each browser: open ${URL}ca.crt and install it as a trusted ROOT"
say "  (Windows: 'Place all certificates in the following store' -> 'Trusted Root Certification Authorities';"
say "   Firefox: Settings -> Certificates -> Authorities -> Import). Check the fingerprint:"
say "  SHA-256 $(openssl x509 -in "$TLS_DIR/ca.crt" -noout -fingerprint -sha256 | cut -d= -f2)"
say "  SHA-1   $(openssl x509 -in "$TLS_DIR/ca.crt" -noout -fingerprint -sha1 | cut -d= -f2)"
say ""
say "API keys for the models: $WORKER_HOME/worker.env (0600, the worker's only), e.g. ANTHROPIC_API_KEY=..."
say "  then: sudo systemctl restart codecome-worker"
say "More web users: sudo -u $WEB_USER env CODECOME_USERS=$USERS_FILE $PREFIX/.venv/bin/python $PREFIX/tools/adduser.py NAME"
say "Uninstall: sudo $PREFIX/uninstall.sh   (keeps data, archives and users unless --purge)"
