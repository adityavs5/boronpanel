"""Run A feature 9: guard the installer's two hard gates in CI --
shellcheck-clean and `--dry-run` succeeds -- plus a plain syntax check.

These run the real script as a subprocess; anything that needs system tools
not present in a bare CI container is skipped rather than failed.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parent.parent / "scripts" / "install.sh"
LITESPEED_KEY = INSTALLER.parent.parent / "deploy" / "litespeed-repository-key.asc"


def test_installer_exists_and_is_executable():
    assert INSTALLER.is_file()
    # Committed with the executable bit so `bash scripts/install.sh` and a
    # direct `./install.sh` both work.
    assert INSTALLER.stat().st_mode & 0o111


def test_installer_bash_syntax_ok():
    # `bash -n` parses without executing -- no system deps, always runnable.
    r = subprocess.run(["bash", "-n", str(INSTALLER)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_installer_redacts_openlitespeed_generated_password():
    source = INSTALLER.read_text()
    start = source.index("_redact_command_output()")
    end = source.index("\n}\n", start) + 3
    function = source[start:end]
    secret = "TemporaryWebAdminSecret"
    result = subprocess.run(
        ["bash", "-c", function + "\nprintf '%s\\n' \"$1\" | _redact_command_output", "bash",
         f"WebAdmin user/password is admin/{secret}"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert secret not in result.stdout
    assert result.stdout.strip() == "WebAdmin user/password is admin/***"


@pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg not installed")
def test_litespeed_repository_has_pinned_scoped_trust():
    source = INSTALLER.read_text()
    assert "repo.litespeed.sh | bash" not in source
    assert "signed-by=/usr/share/keyrings/boron-litespeed-repository.asc" in source
    assert "https://rpms.litespeedtech.com/debian/ noble main" in source
    assert "LiteSpeed apt cache update" in source
    result = subprocess.run(
        ["gpg", "--show-keys", "--with-colons", str(LITESPEED_KEY)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    fingerprints = [line.split(":")[9] for line in result.stdout.splitlines()
                    if line.startswith("fpr:")]
    assert fingerprints[0] == "3E892522DB44E1B063D366C5011AA62DEDA1F085"


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_installer_shellcheck_clean():
    r = subprocess.run(["shellcheck", str(INSTALLER)], capture_output=True, text=True)
    assert r.returncode == 0, f"shellcheck findings:\n{r.stdout}\n{r.stderr}"


def test_installer_help_exits_zero():
    r = subprocess.run(["bash", str(INSTALLER), "--help"], capture_output=True, text=True)
    assert r.returncode == 0
    assert "Usage:" in r.stdout
    assert "--dry-run" in r.stdout
    assert "--uninstall" in r.stdout


def test_installer_covers_runtime_dependencies_and_firewall_policy():
    """Keep the fresh-install inventory aligned with the services in code."""
    source = INSTALLER.read_text()
    for required in (
        "openlitespeed", "lsphp81", "lsphp82", "lsphp83", "lsphp84", "lsphp85",
        "mariadb-server", "postfix", "dovecot-core", "pdns-server", "pure-ftpd",
        "certbot", "rclone", "spamassassin", "fail2ban", "ufw", "redis-server",
        "python3", "python3-pip", "nodejs", "composer", "imapsync", "geoipupdate",
        "roundcubemail-1.7.4-complete", "setup_webmail",
        "install_filebrowser", "certbot-dns-cloudflare", "boron-filebrowser.service",
    ):
        assert required in source

    # SSH must be admitted before UFW is enabled, and FTP's data range must
    # exactly match Pure-FTPd's configured passive range.
    assert source.index("ufw allow 22/tcp") < source.index("ufw --force enable")
    assert "ufw allow 30000:30100/tcp" in source
    assert "30000 30100" in source
    assert "for p in 21 25 53 80 110 143 443 587 993 995 2222 7080; do" in source
    assert 'run ufw allow "${p}/tcp"' in source
    build_command = "PATH='/opt/boron-nodejs/20/bin':\\\"\\$PATH\\\" npm ci --no-audit --no-fund"
    assert build_command in source
    assert "PATH='/opt/boron-nodejs/20/bin':\\\"\\$PATH\\\" npm run build" in source
    assert source.index("install_node_runtimes", source.index("main()")) < source.index(
        "deploy_app", source.index("main()")
    )
    assert "for _wait in {1..30}" in source
    assert "sudo -u boron-api -- test -r /run/boron/provisiond.sock" in source
    assert "sudo -u boron-api -- test -w /run/boron/provisiond.sock" in source
    assert "provisioning daemon socket was not ready for boron-api after 30 seconds" in source
    assert source.count("mysql --defaults-file=/root/.my.cnf") == 3
    assert "existing_root_pass" in source
    assert "systemctl enable --now clamav-freshclam" in source
    assert "web UI built from frontend source" in source
    filebrowser_source = Path(__file__).resolve().parent.parent.joinpath("daemon/filebrowser.py").read_text()
    assert '["iptables", "-I", "OUTPUT", str(position)]' in filebrowser_source
    assert 'positions 1-2' in filebrowser_source
    assert 'boron-geoip.cron' in source
    assert 'Optional MaxMind GeoLite2 license key' in source

    # The bootstrap password must not appear in create_admin.py's argv or be
    # inherited by every installer child through FH_ADMIN_PASSWORD.
    assert 'args+=(--password' not in source
    assert 'unset FH_ADMIN_PASSWORD' in source
    assert '--password-stdin' in source
    assert "printf '%s' \"$ADMIN_PASSWORD\"" in source
    assert 'chmod 600 "$INSTALL_LOG"' in source
    assert 'WebAdmin user/password is' in source
    assert '_append_command_output "$output"' in source
    assert 'cat "$output" >>"$INSTALL_LOG"' not in source

    # Namespace isolation is enabled by the generated OLS config.  The
    # namespace template and its companion lsns state must exist before the
    # first lshttpd start, or every fresh account provision fails validation.
    assert "write_file /usr/local/lsws/conf/nsconf.conf 0644" in source
    assert "$PASSWD,nobody,mysql" in source
    assert "$GROUP,nogroup,mysql" in source
    assert "write_file /usr/local/lsws/lsns/conf/lsns.conf 0644" in source
    assert "1000" in source[source.index("write_file /usr/local/lsws/lsns/conf/lsns.conf"):]
    assert source.index("setup_ols_namespace") < source.index("run systemctl enable --now lshttpd")
    assert "write_file /etc/systemd/system/lshttpd.service.d/boron-lifecycle.conf 0644" in source
    assert "KillMode=mixed" in source


def test_installer_rejects_unknown_flag():
    r = subprocess.run(["bash", str(INSTALLER), "--bogus"], capture_output=True, text=True)
    assert r.returncode != 0
    assert "unknown option" in r.stderr


@pytest.mark.skipif(
    shutil.which("ss") is None or shutil.which("df") is None,
    reason="needs iproute2 (ss) + coreutils (df) for the dry-run pre-flight",
)
def test_installer_dry_run_succeeds():
    r = subprocess.run(
        ["bash", str(INSTALLER), "--dry-run"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "FH_NONINTERACTIVE": "1"},
    )
    assert r.returncode == 0, f"dry-run failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}"
    assert "dry-run" in r.stdout
    assert "Pre-flight checks" in r.stdout
    # The plan must reach the final step and never actually mutate.
    assert "Summary:" in r.stdout
    assert "0 failed" in r.stdout
