"""The first-admin bootstrap keeps its password out of process arguments."""
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "create_admin.py"


def test_create_admin_exposes_stdin_mode_and_no_password_argument():
    result = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "--password-stdin" in result.stdout
    assert "--password PASSWORD" not in result.stdout


def test_create_admin_rejects_oversized_stdin_before_rpc():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--username", "admin", "--password-stdin"],
        input="x" * 257, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "exceeds 256 characters" in result.stderr
