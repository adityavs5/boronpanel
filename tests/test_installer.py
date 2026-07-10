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
