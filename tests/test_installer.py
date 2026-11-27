# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Static checks of install.sh / uninstall.sh and the unit templates (the real run needs root and a
clean Debian/Ubuntu: tools/test-install-container.sh)."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = sorted((ROOT / "deploy/templates").glob("*.service.in"))


def test_scripts_parse():
    for s in ("install.sh", "uninstall.sh", "deploy/tls-setup.sh"):
        assert subprocess.run(["bash", "-n", str(ROOT / s)]).returncode == 0, s


def test_help_works_without_root():
    out = subprocess.run(["bash", str(ROOT / "install.sh"), "--help"], capture_output=True, text=True)
    assert out.returncode == 0 and "--codecome" in out.stdout and "--yes" in out.stdout


def test_every_template_placeholder_is_filled_by_the_installer():
    filled = set(re.findall(r"s\|@([A-Z_]+)@\|", (ROOT / "install.sh").read_text()))
    for t in TEMPLATES:
        used = set(re.findall(r"@([A-Z_]+)@", t.read_text()))
        assert used and used <= filled, (t.name, sorted(used - filled))


def test_rendered_units_keep_the_hardening():
    values = {"WEB_USER": "ccweb", "WORKER_USER": "ccwork", "GROUP": "cc", "WEB_GROUP": "ccweb", "WORKER_GROUP": "ccwork",
              "PREFIX": "/opt/ccw", "DATA": "/opt/ccw/data", "ARCHIVES": "/opt/cca", "CODECOME": "/opt/CodeCome",
              "HOST": "10.0.0.5", "PORT": "8100", "TLS_DIR": "/etc/codecome-web/tls", "ADMINS": "admin",
              "WORKER_HOME": "/var/lib/ccwork", "PROTECT_HOME": "true", "WORKER_RW": ""}
    units = {}
    for t in TEMPLATES:
        text = re.sub(r"@([A-Z_]+)@", lambda m: values[m.group(1)], t.read_text())
        units[t.name] = text
    web, worker = units["codecome-web.service.in"], units["codecome-worker.service.in"]
    assert "--ssl-keyfile /etc/codecome-web/tls/server.key" in web and "CODECOME_COOKIE_SECURE=1" in web
    assert "ProtectSystem=strict" in web and "ReadWritePaths=/opt/ccw/data" in web and "NoNewPrivileges=true" in web
    assert "ProtectSystem=full" in worker and "NoNewPrivileges=true" in worker
    assert "EnvironmentFile=-/var/lib/ccwork/worker.env" in worker and "API" not in web.split("[Service]")[1].split("ExecStart")[0].replace("CODECOME", "")


def test_uninstall_never_removes_packages_or_codecome():
    text = (ROOT / "uninstall.sh").read_text()
    assert "apt-get remove" not in text and "apt remove" not in text and "purge -y" not in text
    assert not re.search(r"rm -rf \"?\$\{?CODECOME", text)


def test_manifest_merge_keeps_previous_entries_with_pipes(tmp_path):
    """Upgrades merge what earlier runs created; ACL entries contain '|' (this broke a sed once)."""
    script = (ROOT / "install.sh").read_text()
    func = script[script.index("merged() {"):script.index("\n}\n", script.index("merged() {")) + 3]
    m = tmp_path / "manifest"
    m.write_text("ACLS=/opt/CodeCome|u:codecome|R /home/x|u:web\nCREATED_USERS=codecome codecomeweb\n")
    prog = (f"set -euo pipefail; MANIFEST={m}\n{func}\nA=(); B=('/opt/CodeCome|u:codecome|R' '/new|u:w')\n"
            'echo "$(merged ACLS "${B[@]}")"; echo "$(merged CREATED_USERS "${A[@]}")"; echo "[$(merged NOPE "${A[@]}")]"')
    out = subprocess.run(["bash", "-c", prog], capture_output=True, text=True, check=True).stdout.splitlines()
    assert out == ["/home/x|u:web /new|u:w /opt/CodeCome|u:codecome|R", "codecome codecomeweb", "[]"]
