import os
import json

import pytest

from daemon.snapshot_mail_files import prepare_maildir
from shared.validation import ValidationError
from types import SimpleNamespace


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


@pytest.mark.skipif(os.geteuid() != 0, reason='Worker drops root privileges')
@pytest.mark.parametrize('failure', ['open', 'backup', 'timeout'])
def test_worker_failure_removes_workspace_and_preserves_snapshot(staged, monkeypatch, failure):
    import daemon.snapshot_mail_files as files
    source, target, root = staged
    workspaces = []
    calls = []
    original = (source / 'cur/message:2,S').read_bytes()

    def run(args, **kwargs):
        work = files.Path(kwargs['cwd'])
        workspaces.append(work)
        calls.append(args)
        assert work.stat().st_mode & 0o777 == 0o710
        assert '--no-new-privs' in args and '--clear-groups' in args
        assert '--reuid=65534' in args and '--regid=65534' in args
        assert kwargs['discard_stdout'] is True
        if failure == 'timeout':
            from subprocess import TimeoutExpired
            raise TimeoutExpired(args, 1)
        return SimpleNamespace(ok=failure == 'backup' and len(calls) == 1)

    monkeypatch.setattr(files, 'run', run)
    from subprocess import TimeoutExpired
    with pytest.raises((ValidationError, TimeoutExpired)):
        files.build_maildir(source, target, root, uid=65534, gid=65534)
    assert workspaces and all(not path.exists() for path in workspaces)
    assert not target.exists()
    assert (source / 'cur/message:2,S').read_bytes() == original


@pytest.mark.parametrize('uid,gid', [(0, 150), (150, 0), (True, 150), (150, -1)])
def test_worker_rejects_privileged_or_invalid_identity(staged, uid, gid):
    from daemon.snapshot_mail_files import build_maildir
    with pytest.raises(ValidationError):
        build_maildir(*staged, uid=uid, gid=gid)
    assert not staged[1].exists()


@pytest.fixture
def mailbox_home(tmp_path, monkeypatch):
    if os.geteuid() != 0:
        pytest.skip('Mailbox staging sets unprivileged ownership')
    from shared.config import settings
    base = tmp_path / 'mail'
    home = base / 'example.test/inbox'
    (home / 'Maildir/cur').mkdir(parents=True)
    (home / 'Maildir/new').mkdir()
    (home / 'Maildir/tmp').mkdir()
    (home / 'Maildir/cur/current').write_bytes(b'retain live mail')
    monkeypatch.setattr(settings, 'mail_base', str(base))
    return home


def test_stage_for_exchange_sets_ownership_and_preserves_live_mail(staged, mailbox_home):
    from daemon.snapshot_mail_files import stage_for_exchange
    source, _, root = staged
    result = stage_for_exchange(source, root, 'example.test', 'inbox', uid=65534, gid=65534)
    target = mailbox_home / result['prepared']
    assert result['files'] == 4
    for path in [target, *target.rglob('*')]:
        info = path.stat()
        assert (info.st_uid, info.st_gid) == (65534, 65534)
        assert info.st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
        if path.is_file():
            assert path.read_bytes() == (source / path.relative_to(target)).read_bytes()
    assert (mailbox_home / 'Maildir/cur/current').read_bytes() == b'retain live mail'
    assert (source / 'cur/message:2,S').stat().st_uid == 0


@pytest.mark.parametrize('bad', ['link', 'fifo', 'disk-full'])
def test_failed_staging_removes_only_new_sibling(staged, mailbox_home, monkeypatch, bad):
    import daemon.snapshot_mail_files as files
    source, _, root = staged
    if bad == 'link':
        (source / 'cur/link').symlink_to('/etc/passwd')
    elif bad == 'fifo':
        os.mkfifo(source / 'cur/fifo')
    else:
        def fail(*args, **kwargs):
            raise OSError('disk full')
        monkeypatch.setattr(files.shutil, 'copyfileobj', fail)
    with pytest.raises((OSError, ValidationError)):
        files.stage_for_exchange(source, root, 'example.test', 'inbox', uid=65534, gid=65534)
    assert list(mailbox_home.iterdir()) == [mailbox_home / 'Maildir']
    assert (mailbox_home / 'Maildir/cur/current').read_bytes() == b'retain live mail'


def test_staging_rejects_mailbox_home_symlink(staged, mailbox_home):
    from daemon.snapshot_mail_files import stage_for_exchange
    source, _, root = staged
    retained = mailbox_home.with_name('retained')
    mailbox_home.rename(retained)
    mailbox_home.symlink_to(retained, target_is_directory=True)
    with pytest.raises(OSError):
        stage_for_exchange(source, root, 'example.test', 'inbox', uid=65534, gid=65534)
    assert list(retained.iterdir()) == [retained / 'Maildir']


def test_persisted_prepared_name_is_exclusive(staged, mailbox_home):
    from daemon.snapshot_mail_files import stage_for_exchange
    source, _, root = staged
    name = '.boron-mail-ready-' + 'd'*32
    result = stage_for_exchange(source, root, 'example.test', 'inbox',
                               uid=65534, gid=65534, prepared=name)
    assert result['prepared'] == name
    with pytest.raises(FileExistsError):
        stage_for_exchange(source, root, 'example.test', 'inbox',
                           uid=65534, gid=65534, prepared=name)
    assert (mailbox_home/name/'cur/message:2,S').read_bytes() == (source/'cur/message:2,S').read_bytes()


def test_placement_receipt_is_persisted_before_copy_and_marks_ready(staged, mailbox_home, monkeypatch):
    import daemon.snapshot_mail_files as files
    source, _, root = staged
    receipt = root / 'placement.json'
    original = files._copy_mail_tree
    observed = []
    def copy(source_fd, target_fd, uid, gid, counts):
        record = json.loads(receipt.read_text())
        assert record['status'] == 'copying' and record['restore_id'] == 7
        assert record['home'] == [mailbox_home.stat().st_dev, mailbox_home.stat().st_ino]
        target = mailbox_home / record['prepared']
        assert record['identity'] == [target.stat().st_dev, target.stat().st_ino]
        observed.append(True)
        return original(source_fd, target_fd, uid, gid, counts)
    monkeypatch.setattr(files, '_copy_mail_tree', copy)
    result = files.stage_for_exchange(source, root, 'example.test', 'inbox', uid=65534, gid=65534,
                                     receipt=receipt, restore_id=7)
    record = json.loads(receipt.read_text())
    assert observed and record['status'] == 'ready'
    assert record['prepared'] == result['prepared'] and record['files'] == 4
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert files.inspect_placement(receipt, root) == 'ready'


def test_placement_crash_retains_record_of_incomplete_copy(staged, mailbox_home):
    import daemon.snapshot_mail_files as files
    source, _, root = staged
    receipt = root / 'placement.json'
    pid = os.fork()
    if pid == 0:
        try:
            files._copy_mail_tree = lambda *args: os._exit(91)
            files.stage_for_exchange(source, root, 'example.test', 'inbox', uid=65534, gid=65534,
                                     receipt=receipt, restore_id=7)
        finally:
            os._exit(92)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 91
    record = json.loads(receipt.read_text())
    assert record['status'] == 'copying'
    target = mailbox_home / record['prepared']
    assert record['identity'] == [target.stat().st_dev, target.stat().st_ino]
    assert files.inspect_placement(receipt, root) == 'copying'
    assert (mailbox_home / 'Maildir/cur/current').read_bytes() == b'retain live mail'


def test_existing_receipt_prevents_another_placement(staged, mailbox_home):
    from daemon.snapshot_mail_files import stage_for_exchange
    source, _, root = staged
    receipt = root / 'placement.json'
    receipt.write_text('retain original job record')
    receipt.chmod(0o600)
    with pytest.raises(FileExistsError):
        stage_for_exchange(source, root, 'example.test', 'inbox', uid=65534, gid=65534,
                           receipt=receipt, restore_id=7)
    assert list(mailbox_home.iterdir()) == [mailbox_home / 'Maildir']
    assert receipt.read_text() == 'retain original job record'


def test_placement_inspection_recognizes_exchanged_tree(staged, mailbox_home):
    from daemon.snapshot_mail_files import stage_for_exchange, inspect_placement
    from daemon import snapshot_mail_exchange as exchange
    source, _, root = staged
    receipt = root / 'placement.json'
    result = stage_for_exchange(source, root, 'example.test', 'inbox', uid=65534, gid=65534,
                                receipt=receipt, restore_id=7)
    plan = exchange.plan('example.test', 'inbox', result['prepared'])
    exchange.apply('example.test', 'inbox', plan)
    assert inspect_placement(receipt, root) == 'exchanged'
    exchange.apply('example.test', 'inbox', plan, undo=True)
    assert inspect_placement(receipt, root) == 'ready'


def test_placement_inspection_rejects_substituted_sibling(staged, mailbox_home):
    from daemon.snapshot_mail_files import stage_for_exchange, inspect_placement
    source, _, root = staged
    receipt = root / 'placement.json'
    result = stage_for_exchange(source, root, 'example.test', 'inbox', uid=65534, gid=65534,
                                receipt=receipt, restore_id=7)
    target = mailbox_home / result['prepared']
    target.rename(mailbox_home / 'retained')
    target.mkdir()
    with pytest.raises(ValidationError):
        inspect_placement(receipt, root)
