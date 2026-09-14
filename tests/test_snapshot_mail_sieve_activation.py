import base64
from copy import deepcopy
import os

import pytest

from daemon import mail, snapshot_mail_guard as guard, snapshot_mail_sieve as sieve
from shared.config import settings
from shared.validation import ValidationError
from tests.test_snapshot_mail_sieve import mail_home


@pytest.fixture
def guarded(mail_home, tmp_path, monkeypatch):
    if os.geteuid() != 0:
        pytest.skip('Real guard and vmail ownership require root')
    account, other, home = mail_home
    monkeypatch.setattr(mail, 'VMAIL_UID', 150)
    monkeypatch.setattr(mail, 'VMAIL_GID', 150)
    os.chown(home.parent, 150, 150); os.chown(home, 150, 150)
    monkeypatch.setattr(settings, 'mail_restore_guard_dir', str(tmp_path / 'guards'))
    entries = []
    for local in ('inbox', 'unused'):
        token = guard.block('alpha.example.test', local, 71)
        entries.append(dict(domain='alpha.example.test', local_part=local, token=token))
    return account, home, entries


def write_script(home, content):
    path = home / sieve.SCRIPT_NAME
    path.write_bytes(content); os.chown(path, 150, 150); path.chmod(0o600)


def document(account, **scripts):
    return dict(format=1, account_id=account.id, username=account.username, scripts=[
        dict(domain='alpha.example.test', local_part=local,
             script_base64=base64.b64encode(content).decode('ascii') if content is not None else None)
        for local, content in scripts.items()])


def guards_retained(entries):
    with guard.owned_guards(entries, 71): pass


def test_guarded_activation_and_exact_custom_script_undo(guarded):
    account, home, entries = guarded
    original = b'# custom comment \xff\r\nkeep;\r\n'
    write_script(home, original)
    cache = home / '.dovecot.svbin'; cache.write_bytes(b'old cache')
    unrelated = home / 'other-filter.sieve'; unrelated.write_bytes(b'keep;\n')
    before = sieve.capture(account, ['inbox@alpha.example.test'])
    wanted = document(account, inbox=b'# restored reply\nkeep;\n')
    checkpoints = []
    result = sieve._apply(account, wanted, before, 71, entries[:1], checkpoints.append)
    assert result == {'mailboxes': ['inbox@alpha.example.test']}
    assert checkpoints == result['mailboxes']
    info = (home / sieve.SCRIPT_NAME).stat()
    assert (info.st_uid, info.st_gid, info.st_mode & 0o777) == (150, 150, 0o600)
    assert not cache.exists() and unrelated.read_bytes() == b'keep;\n'
    guards_retained(entries)
    sieve._apply(account, before, wanted, 71, entries[:1], checkpoints.append)
    assert (home / sieve.SCRIPT_NAME).read_bytes() == original
    guards_retained(entries)


def test_lazy_home_activation_and_undo_to_absence(guarded):
    account, home, entries = guarded
    before = sieve.capture(account, ['unused@alpha.example.test'])
    wanted = document(account, unused=b'keep;\n')
    target = home.parent / 'unused'
    assert not target.exists()
    sieve._apply(account, wanted, before, 71, entries[1:], lambda address: None)
    assert (target / sieve.SCRIPT_NAME).read_bytes() == b'keep;\n'
    info = target.stat()
    assert (info.st_uid, info.st_gid, info.st_mode & 0o777) == (150, 150, 0o700)
    sieve._apply(account, before, wanted, 71, entries[1:], lambda address: None)
    assert not (target / sieve.SCRIPT_NAME).exists()
    guards_retained(entries)


def test_second_script_compile_failure_prevents_all_activation(guarded):
    account, home, entries = guarded
    write_script(home, b'keep;\n')
    before = sieve.capture(account, ['inbox@alpha.example.test', 'unused@alpha.example.test'])
    wanted = document(account, inbox=b'# new\nkeep;\n', unused=b'not valid sieve {{{')
    with pytest.raises(ValidationError, match='compile'):
        sieve._apply(account, wanted, before, 71, entries, lambda address: pytest.fail('Unexpected activation'))
    assert (home / sieve.SCRIPT_NAME).read_bytes() == b'keep;\n'
    assert not (home.parent / 'unused').exists()
    guards_retained(entries)


def test_wrong_guard_owner_prevents_activation(guarded):
    account, home, entries = guarded
    before = document(account, inbox=None)
    wrong = deepcopy(entries[:1]); wrong[0]['token'] = 'b' * 64
    with pytest.raises(ValidationError, match='another job'):
        sieve._apply(account, document(account, inbox=b'keep;\n'), before, 71, wrong, lambda address: None)
    assert not (home / sieve.SCRIPT_NAME).exists()
    guards_retained(entries)


def test_changed_script_after_safety_copy_prevents_all_writes(guarded):
    account, home, entries = guarded
    before = document(account, inbox=None, unused=None)
    write_script(home, b'# newer custom script\nkeep;\n')
    with pytest.raises(ValidationError, match='changed after its safety copy'):
        sieve._apply(account, document(account, inbox=b'keep;\n', unused=b'keep;\n'), before,
                     71, entries, lambda address: None)
    assert (home / sieve.SCRIPT_NAME).read_bytes() == b'# newer custom script\nkeep;\n'
    assert not (home.parent / 'unused').exists()
    guards_retained(entries)


def test_partial_batch_retains_guards_and_can_restore_exact_previous_state(guarded, monkeypatch):
    account, home, entries = guarded
    write_script(home, b'# original\nkeep;\n')
    before = sieve.capture(account, ['inbox@alpha.example.test', 'unused@alpha.example.test'])
    wanted = document(account, inbox=b'# new\nkeep;\n', unused=b'keep;\n')
    original_write = sieve._write_script
    calls = []
    def fail_second(descriptor, content):
        if calls: raise OSError('simulated second-script failure')
        original_write(descriptor, content); calls.append(True)
    monkeypatch.setattr(sieve, '_write_script', fail_second)
    completed = []
    with pytest.raises(OSError, match='second-script'):
        sieve._apply(account, wanted, before, 71, entries, completed.append)
    assert completed == ['inbox@alpha.example.test']
    guards_retained(entries)
    actual = sieve.capture(account, ['inbox@alpha.example.test', 'unused@alpha.example.test'])
    assert actual != before and actual != wanted
    monkeypatch.setattr(sieve, '_write_script', original_write)
    sieve._apply(account, before, actual, 71, entries, lambda address: None)
    assert sieve.capture(account, ['inbox@alpha.example.test', 'unused@alpha.example.test']) == before
    guards_retained(entries)


def test_failed_atomic_replace_keeps_original_and_cleans_temporary(guarded, monkeypatch):
    account, home, entries = guarded
    write_script(home, b'keep;\n')
    before = sieve.capture(account, ['inbox@alpha.example.test'])
    def fail(*args, **kwargs): raise OSError('simulated rename failure')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError, match='rename failure'):
        sieve._apply(account, document(account, inbox=b'# new\nkeep;\n'), before, 71, entries[:1], lambda address: None)
    assert (home / sieve.SCRIPT_NAME).read_bytes() == b'keep;\n'
    assert not list(home.glob('.boron-sieve-*'))
    guards_retained(entries)


def test_checkpoint_failure_retains_guard_and_observable_applied_state(guarded):
    account, home, entries = guarded
    wanted = document(account, inbox=b'keep;\n')
    def fail(address): raise RuntimeError('checkpoint unavailable')
    with pytest.raises(RuntimeError, match='checkpoint unavailable'):
        sieve._apply(account, wanted, document(account, inbox=None), 71, entries[:1], fail)
    assert sieve.capture(account, ['inbox@alpha.example.test']) == wanted
    guards_retained(entries)


def test_unsafe_compiled_cache_stops_activation(guarded, tmp_path):
    account, home, entries = guarded
    outside = tmp_path / 'outside'; outside.write_bytes(b'unchanged')
    (home / '.dovecot.svbin').symlink_to(outside)
    with pytest.raises(ValidationError, match='unsafe file type'):
        sieve._apply(account, document(account, inbox=b'keep;\n'), document(account, inbox=None),
                     71, entries[:1], lambda address: None)
    assert outside.read_bytes() == b'unchanged' and not (home / sieve.SCRIPT_NAME).exists()
    guards_retained(entries)


@pytest.mark.parametrize('variant', ['account', 'encoding', 'duplicate', 'selection', 'guards'])
def test_invalid_private_activation_document_cannot_write(guarded, variant):
    account, home, entries = guarded
    wanted = document(account, inbox=b'keep;\n')
    before = document(account, inbox=None)
    selected_guards = entries[:1]
    if variant == 'account': wanted['account_id'] += 1
    elif variant == 'encoding': wanted['scripts'][0]['script_base64'] = 'not base64!'
    elif variant == 'duplicate': wanted['scripts'] *= 2
    elif variant == 'selection': before = document(account, unused=None)
    elif variant == 'guards': selected_guards = entries[1:]
    with pytest.raises(ValidationError):
        sieve._apply(account, wanted, before, 71, selected_guards, lambda address: None)
    assert not (home / sieve.SCRIPT_NAME).exists()
    guards_retained(entries)
