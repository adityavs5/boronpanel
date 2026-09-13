import os

import pytest

from daemon.snapshot_mail_files import prepare_maildir
from shared.validation import ValidationError


@pytest.fixture
def staged(tmp_path):
    tmp_path.chmod(0o700)
    source = tmp_path / 'restored'
    for folder in ('cur', 'new', 'tmp', '.Archive/cur', '.Archive/new', '.Archive/tmp'):
        (source / folder).mkdir(parents=True, exist_ok=True)
    (source / 'cur/message:2,S').write_bytes(b'original\x00message\r\n')
    (source / '.Archive/cur/archive:2,SF').write_bytes(b'archived')
    (source / 'dovecot-uidlist').write_text('3 V100 N2 Gfixture\n1 :message\n')
    (source / 'subscriptions').write_text('Archive\n')
    return source, tmp_path / 'prepared', tmp_path


def test_preparation_retains_bytes_names_and_indexes_privately(staged):
    source, target, root = staged
    result = prepare_maildir(*staged)
    assert result['files'] == 4
    assert result['bytes'] == sum(p.stat().st_size for p in source.rglob('*') if p.is_file())
    for path in source.rglob('*'):
        copied = target / path.relative_to(source)
        assert copied.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
        if path.is_file():
            assert copied.read_bytes() == path.read_bytes()
            assert copied.stat().st_ino != path.stat().st_ino


@pytest.mark.parametrize('kind', ['file-link', 'directory-link', 'fifo'])
def test_unsafe_entries_fail_before_destination_creation(staged, kind):
    source, target, root = staged
    if kind == 'file-link':
        (source / 'cur/link').symlink_to('/etc/passwd')
    elif kind == 'directory-link':
        (source / '.Other').symlink_to('/tmp', target_is_directory=True)
    else:
        os.mkfifo(source / 'new/pipe')
    with pytest.raises(ValidationError):
        prepare_maildir(*staged)
    assert not target.exists()


def test_existing_destination_is_retained(staged):
    source, target, root = staged
    target.mkdir()
    (target / 'retained').write_text('keep')
    with pytest.raises(ValidationError):
        prepare_maildir(*staged)
    assert (target / 'retained').read_text() == 'keep'


def test_nonprivate_staging_rejected(staged):
    source, target, root = staged
    root.chmod(0o755)
    with pytest.raises(ValidationError):
        prepare_maildir(*staged)
    assert not target.exists()


def test_incomplete_maildir_rejected(staged):
    source, target, root = staged
    (source / 'new').rmdir()
    with pytest.raises(ValidationError):
        prepare_maildir(*staged)
    assert not target.exists()


def test_copy_failure_removes_only_new_staging(staged, monkeypatch):
    source, target, root = staged
    import daemon.snapshot_mail_files as files
    def fail(*args, **kwargs):
        raise OSError('simulated disk full')
    monkeypatch.setattr(files.shutil, 'copyfileobj', fail)
    with pytest.raises(OSError):
        prepare_maildir(*staged)
    assert not target.exists()
    assert (source / 'cur/message:2,S').read_bytes() == b'original\x00message\r\n'


@pytest.mark.parametrize('location', ['outside', 'inside-source', 'parent-link'])
def test_destination_boundaries(staged, location, tmp_path_factory):
    source, target, root = staged
    outside = tmp_path_factory.mktemp('other-mail-stage')
    if location == 'outside':
        target = outside / 'prepared'
    elif location == 'inside-source':
        target = source / 'prepared'
    else:
        (root / 'link').symlink_to(outside, target_is_directory=True)
        target = root / 'link/prepared'
    with pytest.raises(ValidationError):
        prepare_maildir(source, target, root)
    assert not target.exists()


def test_hardlinked_messages_become_independent_copies(staged):
    source, target, root = staged
    os.link(source / 'cur/message:2,S', source / '.Archive/cur/linked:2,S')
    prepare_maildir(*staged)
    first = target / 'cur/message:2,S'
    second = target / '.Archive/cur/linked:2,S'
    assert first.read_bytes() == second.read_bytes()
    assert first.stat().st_ino != second.stat().st_ino
