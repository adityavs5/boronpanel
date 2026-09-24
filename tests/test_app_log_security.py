import importlib.util
import os
from pathlib import Path

import pytest

from daemon import appunits, safeio
from shared.config import settings


@pytest.fixture
def log_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'home_base', str(tmp_path / 'home'))
    folder = tmp_path / 'home/alice/logs/node'
    folder.mkdir(parents=True)
    return folder


def test_log_reader_denies_symlinks_and_special_files(log_tree, tmp_path):
    secret = tmp_path / 'private'
    secret.write_text('protected canary')
    log = log_tree / 'site.log'
    log.symlink_to(secret)
    with pytest.raises(OSError):
        appunits.tail_log_file(str(log))
    log.unlink()
    os.mkfifo(log)
    with pytest.raises(safeio.UnsafePathError):
        appunits.tail_log_file(str(log))


def test_log_reader_denies_ancestor_symlink(log_tree, tmp_path):
    log_tree.rmdir()
    log_tree.symlink_to(tmp_path, target_is_directory=True)
    (tmp_path / 'site.log').write_text('protected canary')
    with pytest.raises(safeio.UnsafePathError):
        appunits.tail_log_file(str(log_tree / 'site.log'))


def test_log_reader_bounds_tail(log_tree):
    log = log_tree / 'site.log'
    log.write_text('x' * 1024 * 1024 + '\nlast line\n')
    assert appunits.tail_log_file(str(log)) == ['last line']


@pytest.mark.parametrize('kind', ['fifo', 'hardlink'])
def test_log_provision_refuses_special_or_shared_file(log_tree, tmp_path, kind):
    target = log_tree / 'site.log'
    private = tmp_path / 'private'
    private.write_text('protected')
    private.chmod(0o600)
    if kind == 'fifo':
        os.mkfifo(target)
    else:
        os.link(private, target)
    with pytest.raises(safeio.UnsafePathError):
        safeio.secure_ensure_file_beneath(str(log_tree.parent), 'node', 'site.log', os.getuid(), os.getgid(), 0o777)
    assert private.stat().st_mode & 0o777 == 0o600


def test_log_exec_refuses_root_before_open(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('app_exec_test', Path(__file__).parents[1] / 'scripts/app_exec.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    target = tmp_path / 'log'
    with pytest.raises(RuntimeError, match='tenant identity'):
        module.main(['--log', str(target), '--', '/bin/true'])
    assert not target.exists()
