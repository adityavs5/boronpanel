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


def test_installer_exists_and_is_executable():
    assert INSTALLER.is_file()
    # Committed with the executable bit so `bash scripts/install.sh` and a
    # direct `./install.sh` both work.
    assert INSTALLER.stat().st_mode & 0o111


def test_installer_bash_syntax_ok():
    # `bash -n` parses without executing -- no system deps, always runnable.
    r = subprocess.run(["bash", "-n", str(INSTALLER)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


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
        "install_filebrowser", "certbot-dns-cloudflare", "boron-filebrowser.service",
    ):
        assert required in source

    # SSH must be admitted before UFW is enabled, and FTP's data range must
    # exactly match Pure-FTPd's configured passive range.
    assert source.index("ufw allow 22/tcp") < source.index("ufw --force enable")
    assert "ufw allow 30000:30100/tcp" in source
    assert "30000 30100" in source
    assert "for p in 21 25 53 80 110 143 443 587 993 995 9443; do" in source
    assert 'run ufw allow "${p}/tcp"' in source
    assert "npm ci --no-audit --no-fund && npm run build" in source
    assert "web UI built from frontend source" in source
    filebrowser_source = Path(__file__).resolve().parent.parent.joinpath("daemon/filebrowser.py").read_text()
    assert '["iptables", "-I", "OUTPUT", str(position)]' in filebrowser_source
    assert 'positions 1-2' in filebrowser_source
    assert 'boron-geoip.cron' in source
    assert 'Optional MaxMind GeoLite2 license key' in source


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
