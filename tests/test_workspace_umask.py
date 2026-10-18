"""The test harness must support service-user fixtures under a root umask."""
import os
from pathlib import Path
import subprocess
import sys


def test_workspace_traversal_and_child_umask(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/test_workspace.py"
    env = dict(os.environ, BORON_TEST_TMPDIR=str(tmp_path / "w"))
    env.pop("BORON_TEST_REPORT_DIR", None)
    # Keep this regression independent of the absolute pytest path length.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="bw-", dir="/tmp") as short:
        env["BORON_TEST_TMPDIR"] = short
        probe = """
import os, pathlib, stat
run = pathlib.Path(os.environ['BORON_TEST_RUN_DIR'])
assert stat.S_IMODE(run.stat().st_mode) == 0o755
assert stat.S_IMODE((run / 't').stat().st_mode) == 0o755
assert stat.S_IMODE((run / 'a').stat().st_mode) == 0o700
assert os.umask(0o022) == 0o022
private = run / 't' / 'private'
private.mkdir(mode=0o700)
assert stat.S_IMODE(private.stat().st_mode) == 0o700
"""
        result = subprocess.run([sys.executable, str(script), "--", sys.executable, "-c", probe],
                                env=env, umask=0o077, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
