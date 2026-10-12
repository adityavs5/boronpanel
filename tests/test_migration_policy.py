import subprocess
import sys
from pathlib import Path

from scripts.check_schema_migrations import scan_revision_source, validate_registry


def test_current_revision_registry_passes_policy():
    assert validate_registry() == []


def test_same_release_add_then_drop_is_rejected():
    source = """
    ALTER TABLE accounts ADD COLUMN temporary_value TEXT;
    ALTER TABLE accounts DROP COLUMN temporary_value;
    """
    assert scan_revision_source(source) == [
        "column 'temporary_value' is added and dropped in the same release"
    ]


def test_same_release_add_then_rename_is_rejected():
    source = """
    ALTER TABLE accounts ADD COLUMN temporary_value TEXT;
    ALTER TABLE accounts RENAME COLUMN temporary_value TO final_value;
    """
    assert scan_revision_source(source) == [
        "column 'temporary_value' is added and renamed in the same release"
    ]


def test_ci_checker_process_rejects_deliberately_breaking_revision(tmp_path):
    revision = tmp_path / "breaking.py"
    revision.write_text(
        "ALTER TABLE accounts ADD COLUMN temporary_value TEXT;\n"
        "ALTER TABLE accounts DROP COLUMN temporary_value;\n"
    )
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/check_schema_migrations.py"), str(revision)],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert "added and dropped in the same release" in result.stderr
