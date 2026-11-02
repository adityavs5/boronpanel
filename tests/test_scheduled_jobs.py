"""Every scheduled job must be able to start (live finding: cron ran most
scripts directly, they ship without an exec bit, and every run of the backup
scheduler, usage snapshot/alerts, inode reconcile, phpMyAdmin token cleanup
and SSL-expiry check died with "Permission denied")."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
VENV_PYTHON = "/opt/boron/.venv/bin/python"
SCRIPT_RE = re.compile(r"/opt/boron/scripts/([A-Za-z0-9_]+\.py)")


def _cron_entries():
    for path in sorted(DEPLOY.glob("*.cron")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" not in stripped.split()[0]:
                yield path.name, number, stripped


def test_every_cron_file_has_entries():
    names = {name for name, _, _ in _cron_entries()}
    assert names == {path.name for path in DEPLOY.glob("*.cron")}


@pytest.mark.parametrize("name,number,line", list(_cron_entries()))
def test_cron_entries_never_depend_on_an_exec_bit(name, number, line):
    fields = line.split(None, 6)
    assert len(fields) == 7 and fields[5] == "root", f"{name}:{number} is not a root /etc/cron.d entry"
    command = fields[6]
    for match in SCRIPT_RE.finditer(command):
        script = match.group(1)
        assert (ROOT / "scripts" / script).is_file(), f"{name}:{number} runs missing {script}"
        prefix = command[:match.start()].rstrip()
        assert prefix.endswith(VENV_PYTHON), (
            f"{name}:{number} must run {script} via {VENV_PYTHON}: release archives "
            "do not carry the exec bit"
        )
    first = command.split()[0]
    assert first.startswith("/"), f"{name}:{number} must use an absolute program path"
    assert not first.startswith("/opt/boron/scripts/"), f"{name}:{number} executes a script directly"


def _cron_scripts():
    return sorted({match.group(1) for _, _, line in _cron_entries() for match in SCRIPT_RE.finditer(line)})


@pytest.mark.parametrize("script", _cron_scripts())
def test_cron_scripts_import_the_release_root(script):
    """`python /opt/boron/scripts/x.py` puts scripts/ on sys.path, not the
    release root, and the venv has no .pth for it: a script that imports
    daemon/shared/api must add the root before its first such import
    (inode_quota_reconcile.py did not, so naming the interpreter alone
    turned "Permission denied" into ModuleNotFoundError)."""
    import ast

    tree = ast.parse((ROOT / "scripts" / script).read_text())
    path_added = False
    for node in tree.body:
        if isinstance(node, ast.Expr) and "sys.path.insert" in ast.unparse(node):
            path_added = True
        modules = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                   else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
        if any(name.split(".")[0] in {"daemon", "shared", "api"} for name in modules):
            assert path_added, f"{script} imports {modules[0]} before adding the release root to sys.path"
            return


def test_cron_script_runs_from_another_directory(tmp_path):
    """End to end: run a cron script the way cron does, from an unrelated cwd,
    with only the module import step (no side effects)."""
    import subprocess
    import sys

    probe = ("import runpy, sys; sys.argv = [sys.argv[1]]; "
             "runpy.run_path(sys.argv[0], run_name='not_main')")
    result = subprocess.run([sys.executable, "-c", probe, str(ROOT / "scripts" / "inode_quota_reconcile.py")],
                            cwd=tmp_path, capture_output=True, text=True, timeout=60,
                            env={"PATH": "/usr/bin:/bin", "BORON_DB_URL": f"sqlite:///{tmp_path}/x.db"})
    assert result.returncode == 0, result.stderr


def test_installer_installs_every_cron_file():
    installer = (ROOT / "scripts" / "install.sh").read_text()
    for path in DEPLOY.glob("*.cron"):
        assert f'deploy/{path.name}"' in installer, path.name


def test_upgrade_refreshes_scheduled_jobs(tmp_path, monkeypatch):
    from scripts import upgrade_runtime as upgrade

    cron_dir = tmp_path / "cron.d"
    cron_dir.mkdir()
    stale = "*/5 * * * * root /opt/boron/scripts/backup_scheduler.py\n"
    (cron_dir / "boron-jobs").write_text(stale)
    (cron_dir / "boron-cloudflare").write_text("*/15 * * * * root /opt/boron/scripts/cloudflare_zone_check.py\n")
    monkeypatch.setattr(upgrade, "CRON_DIR", cron_dir)

    upgrade.configure_scheduled_jobs()

    for name in upgrade.CORE_CRON_JOBS:
        installed = cron_dir / name
        assert installed.read_bytes() == (DEPLOY / f"{name}.cron").read_bytes()
        assert installed.stat().st_mode & 0o777 == 0o644
    assert f"{VENV_PYTHON} /opt/boron/scripts/backup_scheduler.py" in (cron_dir / "boron-jobs").read_text()
    # Optional jobs are refreshed where the profile/operator enabled them,
    # never added where they were not.
    assert (cron_dir / "boron-cloudflare").read_bytes() == (DEPLOY / "boron-cloudflare.cron").read_bytes()
    assert not (cron_dir / "boron-geoip").exists()


def test_scheduled_jobs_are_part_of_the_runtime_rollback(monkeypatch, tmp_path):
    from scripts import upgrade_runtime as upgrade

    monkeypatch.setattr(upgrade, "OLS_VHOST_ROOT", tmp_path / "none")
    monkeypatch.setattr(upgrade, "SYSTEMD_UNIT_DIR", tmp_path / "none")
    monkeypatch.setattr(upgrade, "DEFAULT_APP_ENV_DIR", tmp_path / "none")
    paths = set(upgrade._runtime_paths())
    for name in (*upgrade.CORE_CRON_JOBS, *upgrade.OPTIONAL_CRON_JOBS):
        assert upgrade.CRON_DIR / name in paths


def test_upgrade_main_reconciles_scheduled_jobs():
    source = (ROOT / "scripts" / "upgrade_runtime.py").read_text()
    main = source[source.index("def main("):]
    assert "configure_scheduled_jobs()" in main
