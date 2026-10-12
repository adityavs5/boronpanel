from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install.sh"


def _isolated_installer(tmp_path: Path, *, listener: bool = False) -> tuple[Path, dict[str, str]]:
    """Run installer preflight as if this were a fresh disposable host.

    The repository tests execute on a configured Boron server, so absolute
    install/config paths and live listeners would otherwise make every dry run
    an existing-install rerun instead of exercising the fresh-host branch.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "root"
    script = tmp_path / "install.sh"
    source = INSTALLER.read_text()
    replacements = {
        'readonly DEST="/opt/boron"': f'readonly DEST="{root / "opt-boron"}"',
        'readonly CONF_DIR="/etc/boron"': f'readonly CONF_DIR="{root / "etc-boron"}"',
        'readonly DATA_DIR="/var/lib/boron"': f'readonly DATA_DIR="{root / "var-lib-boron"}"',
        'readonly LOG_DIR="/var/log/boron"': f'readonly LOG_DIR="{root / "var-log-boron"}"',
        'readonly INSTALL_LOG="/var/log/boron-install.log"': f'readonly INSTALL_LOG="{root / "install.log"}"',
        'REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"': f'REPO_ROOT="{ROOT}"',
        '/etc/nginx/nginx.conf': str(root / "conflicts/nginx.conf"),
        '/etc/apache2/apache2.conf': str(root / "conflicts/apache2.conf"),
        '/etc/exim4/update-exim4.conf.conf': str(root / "conflicts/exim.conf"),
        '/etc/bind/named.conf': str(root / "conflicts/named.conf"),
        '/usr/local/bin/wp-cli.phar': str(root / "bin/wp-cli.phar"),
    }
    for old, new in replacements.items():
        source = source.replace(old, new)
    script.write_text(source)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "dpkg-query").write_text(
        '#!/bin/sh\nif [ "$1" = "-S" ]; then echo "fake-listener: /usr/sbin/fake-listener"; exit 0; fi\nexit 1\n'
    )
    (fake_bin / "ss").write_text(
        '#!/bin/sh\necho \'LISTEN 0 10 0.0.0.0:2222 0.0.0.0:* users:(("fake-listener",pid=4242,fd=3))\'\n'
        if listener else '#!/bin/sh\nexit 0\n'
    )
    (fake_bin / "readlink").write_text('#!/bin/sh\necho /usr/sbin/fake-listener\n')
    (fake_bin / "curl").write_text('#!/bin/sh\necho "dry-run attempted network access" >&2\nexit 88\n')
    for executable in fake_bin.iterdir():
        executable.chmod(0o755)
    return script, {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}


def _dry_run(profile: str, tmp_path: Path) -> str:
    installer, base_environment = _isolated_installer(tmp_path / profile)
    environment = {
        **base_environment,
        "FH_NONINTERACTIVE": "1",
        "FH_PANEL_DOMAIN": "panel.example.test",
        "FH_LE_EMAIL": "admin@example.test",
        "FH_SERVER_IP": "192.0.2.10",
    }
    result = subprocess.run(
        ["bash", str(installer), "--dry-run", "--profile", profile],
        cwd=ROOT, env=environment, text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "This was a dry-run -- nothing was changed." in result.stdout
    return result.stdout


@pytest.mark.parametrize("profile", ["minimal", "standard", "full"])
def test_each_install_profile_has_a_complete_dry_run(profile, tmp_path):
    output = _dry_run(profile, tmp_path)
    assert f"Installing the {profile} hosting stack" in output


def test_profiles_select_optional_packages_and_steps(tmp_path):
    minimal = _dry_run("minimal", tmp_path)
    standard = _dry_run("standard", tmp_path)
    full = _dry_run("full", tmp_path)
    minimal_stack = next(line for line in minimal.splitlines() if "apt-get install -y" in line and "certbot" in line)
    standard_stack = next(line for line in standard.splitlines() if "apt-get install -y" in line and "certbot" in line)
    full_stack = next(line for line in full.splitlines() if "apt-get install -y" in line and "certbot" in line)
    assert "mariadb-server" not in minimal_stack
    assert "postfix" not in minimal_stack
    assert "mariadb-server" in standard_stack
    assert "postfix" in standard_stack
    assert "rclone" not in standard_stack
    assert "rclone" in full_stack
    assert "Installing frontend build runtime (Node.js 20)" in minimal
    assert "Installing frontend build runtime (Node.js 20)" in standard
    assert "Installing frontend build runtime (Node.js 20)" in full
    assert "Installing Boron Node.js runtimes" not in minimal
    assert "Installing Boron Node.js runtimes" in full
    assert "Installing WordPress tooling" not in minimal
    assert "Installing WordPress tooling" in full
    assert "Installing Roundcube" not in minimal
    assert "Installing Roundcube" in standard


def test_invalid_install_profile_stops_before_changes():
    result = subprocess.run(
        ["bash", str(INSTALLER), "--dry-run", "--profile", "unknown"],
        cwd=ROOT, text=True, capture_output=True, timeout=20,
    )
    assert result.returncode != 0
    assert "choose minimal, standard, or full" in result.stderr


def test_invalid_noninteractive_admin_password_stops_before_packages(tmp_path):
    installer, environment = _isolated_installer(tmp_path / "bad-password")
    environment.update({
        "FH_NONINTERACTIVE": "1",
        "FH_PANEL_DOMAIN": "panel.example.test",
        "FH_LE_EMAIL": "admin@example.test",
        "FH_SERVER_IP": "192.0.2.10",
        "FH_ADMIN_USER": "qaadmin",
        "FH_ADMIN_PASSWORD": "lettersanddigits123",  # pragma: allowlist secret
    })
    result = subprocess.run(
        ["bash", str(installer), "--profile", "minimal"],
        cwd=ROOT, env=environment, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode != 0
    assert "password must contain at least one uppercase letter" in result.stderr
    assert "before installation" in result.stderr
    assert "apt cache updated" not in result.stdout


def test_fresh_install_port_conflict_names_process_and_package(tmp_path):
    installer, environment = _isolated_installer(tmp_path / "conflict", listener=True)
    environment.update({
        "FH_NONINTERACTIVE": "1", "FH_PANEL_DOMAIN": "panel.example.test",
        "FH_LE_EMAIL": "admin@example.test", "FH_SERVER_IP": "192.0.2.10",
    })
    result = subprocess.run(
        ["bash", str(installer), "--dry-run", "--profile", "minimal"],
        cwd=ROOT, env=environment, text=True, capture_output=True, timeout=30,
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "2222: /usr/sbin/fake-listener (package fake-listener)" in combined
    assert "required ports are already owned" in combined
