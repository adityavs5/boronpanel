"""Scoped LiteSpeed APT trust migration without touching the host APT state."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.reconcile_litespeed_repo import (
    KEY_TARGET, LEGACY_FILES, REPO_LINE, SOURCE_TARGET, reconcile,
)


KEY = Path(__file__).resolve().parent.parent / "deploy/litespeed-repository-key.asc"


def _old_host(root: Path) -> None:
    (root / "usr/local/lsws").mkdir(parents=True)
    source = root / LEGACY_FILES[0]
    source.parent.mkdir(parents=True)
    source.write_text("deb http://rpms.litespeedtech.com/debian/ noble main\n"
                      "#deb http://rpms.litespeedtech.com/edge/debian/ noble main\n")
    legacy_key = root / LEGACY_FILES[2]
    legacy_key.parent.mkdir(parents=True)
    legacy_key.write_bytes(KEY.read_bytes())


def test_migration_scopes_key_and_preserves_originals(tmp_path):
    root = tmp_path / "host"
    backup = tmp_path / "protected"
    _old_host(root)
    calls = []
    reconcile(root=root, backup_dir=backup, apt_update=lambda: calls.append("verified"))
    assert calls == ["verified"]
    assert (root / SOURCE_TARGET).read_text() == REPO_LINE
    assert (root / KEY_TARGET).read_bytes() == KEY.read_bytes()
    assert all(not (root / path).exists() for path in LEGACY_FILES)
    assert (backup / "apt-lst_debian_repo.list.before-scoped-trust").exists()
    assert all(path.stat().st_mode & 0o077 == 0 for path in backup.iterdir())
    reconcile(root=root, backup_dir=tmp_path / "second",
              apt_update=lambda: calls.append("unnecessary refresh"))
    assert calls == ["verified"]


def test_apt_failure_restores_exact_previous_files(tmp_path):
    root = tmp_path / "host"
    _old_host(root)
    previous = {path: (root / path).read_bytes() if (root / path).exists() else None
                for path in LEGACY_FILES}

    with pytest.raises(RuntimeError, match="APT verification failed"):
        reconcile(root=root, backup_dir=tmp_path / "protected",
                  apt_update=lambda: (_ for _ in ()).throw(RuntimeError("APT verification failed")))

    assert not (root / SOURCE_TARGET).exists()
    assert not (root / KEY_TARGET).exists()
    for path, content in previous.items():
        if content is None:
            assert not (root / path).exists()
        else:
            assert (root / path).read_bytes() == content


def test_unknown_legacy_source_fails_before_writing(tmp_path):
    root = tmp_path / "host"
    _old_host(root)
    (root / LEGACY_FILES[0]).write_text("deb https://custom.example.invalid/ stable main\n")
    with pytest.raises(RuntimeError, match="unknown LiteSpeed legacy source"):
        reconcile(root=root, backup_dir=tmp_path / "protected", apt_update=lambda: None)
    assert not (root / SOURCE_TARGET).exists()
    assert not (root / KEY_TARGET).exists()
