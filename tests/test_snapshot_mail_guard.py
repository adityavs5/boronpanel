import os

import pytest

from daemon import snapshot_mail_guard as guard
from shared.config import settings
from shared.validation import ValidationError


@pytest.fixture
def directory(tmp_path, monkeypatch):
    if os.geteuid() != 0:
        pytest.skip('Root-owned guard storage required')
    path = tmp_path / 'guards'
    monkeypatch.setattr(settings, 'mail_restore_guard_dir', str(path))
    return path


def test_block_survives_worker_lifetime_until_owned_release(directory):
    token = guard.block('example.test', 'inbox', 1)
    path = directory / guard.marker_name('example.test', 'inbox')
    assert path.is_file() and path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValidationError):
        guard.block('example.test', 'inbox', 2)
    guard.release('example.test', 'inbox', 1, token)
    assert not path.exists()


@pytest.mark.parametrize('wrong', ['job', 'token', 'corrupt'])
def test_wrong_or_corrupt_ownership_retains_guard(directory, wrong):
    token = guard.block('example.test', 'inbox', 1)
    path = directory / guard.marker_name('example.test', 'inbox')
    if wrong == 'corrupt':
        path.write_text('{partial')
    with pytest.raises(ValidationError):
        guard.release('example.test', 'inbox', 2 if wrong == 'job' else 1,
                      'wrong' if wrong == 'token' else token)
    assert path.exists()


def test_stale_release_cannot_remove_later_job(directory):
    token = guard.block('example.test', 'inbox', 1)
    guard.release('example.test', 'inbox', 1, token)
    current = guard.block('example.test', 'inbox', 2)
    with pytest.raises(ValidationError):
        guard.release('example.test', 'inbox', 1, token)
    guard.release('example.test', 'inbox', 2, current)


def test_other_mailbox_guard_retained(directory):
    token = guard.block('example.test', 'inbox', 1)
    other = guard.block('example.test', 'other', 2)
    guard.release('example.test', 'inbox', 1, token)
    assert (directory / guard.marker_name('example.test', 'other')).exists()
    guard.release('example.test', 'other', 2, other)


def test_unsafe_directory_rejected(directory):
    directory.mkdir(mode=0o777)
    directory.chmod(0o777)
    with pytest.raises(ValidationError):
        guard.block('example.test', 'inbox', 1)
    assert list(directory.iterdir()) == []
