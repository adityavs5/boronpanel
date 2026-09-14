from copy import deepcopy
import json
import os

import pytest

from daemon import snapshot_jobs as jobs, snapshot_storage as storage, snapshot_mail_routing as routing
from daemon import snapshot_mail_routing_recovery as recovery, snapshot_mail_metadata
from shared.config import settings
from shared.models import SnapshotDestination
from shared.validation import ValidationError
from tests.test_snapshot_databases import sql_server, sql
from tests.test_snapshot_mail_metadata import mail_database
from tests.test_snapshot_mail_routing_sql import routing_account
from tests.test_snapshot_jobs import make_destination


@pytest.fixture
def prepared(routing_account, mail_database, tmp_path, monkeypatch):
    account, foreign = routing_account
    root = tmp_path / 'vmail'; home = root / 'alpha.example.test' / 'inbox'
    home.mkdir(parents=True)
    os.chown(home.parent, 150, 150); os.chown(home, 150, 150)
    original = b'# custom previous script\r\nkeep;\r\n'
    path = home / '.dovecot.sieve'; path.write_bytes(original)
    os.chown(path, 150, 150); path.chmod(0o600)
    monkeypatch.setattr(settings, 'mail_base', str(root))
    current = routing.capture(account)
    desired = deepcopy(current)
    desired['domains'][0]['autoresponders'][0]['active'] = True
    desired['domains'][0]['forwards'] = []
    plan = recovery.prepare(account, desired)
    assert path.read_bytes() == original and routing.capture(account) == current
    return account, foreign, plan, path


def repository(tmp_path):
    destination = make_destination(tmp_path)
    return jobs.repository(jobs._row(SnapshotDestination, destination['id']))


def test_real_encrypted_routing_script_bundle_round_trip(prepared, mail_database, tmp_path):
    account, _, plan, script = prepared
    _, _, password, hashed = mail_database
    assert password not in repr(plan) and hashed not in repr(plan)
    repo = repository(tmp_path)
    result = recovery.save_previous(repo, account, 81, plan['previous'])
    source = jobs.private_directory('restores', 'restore-81') / 'mail-routing-recovery.json'
    assert source.stat().st_mode & 0o777 == 0o600
    assert password not in source.read_text() and hashed not in source.read_text()
    with pytest.raises(FileExistsError): recovery.save_previous(repo, account, 81, plan['previous'])
    source.unlink()  # Prove the returned state comes from encrypted storage.
    loaded = recovery.load_previous(repo, account, result['snapshot_id'], 81)
    assert loaded == plan['previous']
    assert script.read_bytes() == b'# custom previous script\r\nkeep;\r\n'
    assert list(jobs.private_directory('mail-routing-metadata').iterdir()) == []


def test_foreign_account_and_wrong_job_rejected_before_decryption(prepared, tmp_path, monkeypatch):
    account, foreign, plan, _ = prepared
    repo = repository(tmp_path)
    result = recovery.save_previous(repo, account, 82, plan['previous'])
    def forbidden(*args, **kwargs): raise AssertionError('Must reject before decrypting')
    monkeypatch.setattr(storage, 'restore_to', forbidden)
    with pytest.raises(ValidationError): recovery.load_previous(repo, foreign, result['snapshot_id'], 82)
    with pytest.raises(ValidationError, match='source job'):
        recovery.load_previous(repo, account, result['snapshot_id'], 83)


def test_existing_mail_backup_loads_only_owned_routing(prepared, mail_database, tmp_path):
    from types import SimpleNamespace
    account, foreign, _, _ = prepared
    _, _, password, hashed = mail_database
    repo = repository(tmp_path)
    stage = jobs.private_directory('sources', f'account-{account.id}')
    source = stage / 'mail-recovery.json'
    payload = snapshot_mail_metadata.capture(account.username, [SimpleNamespace(domain='alpha.example.test')])
    source.write_text(json.dumps(payload)); source.chmod(0o600)
    result = storage.backup(repo, account.id, [str(stage)])
    saved = recovery.load_routing(repo, account, result['snapshot_id'], ['alpha.example.test'])
    assert saved == routing.capture(account)
    assert password not in repr(saved) and hashed not in repr(saved)
    with pytest.raises(ValidationError): recovery.load_routing(repo, foreign, result['snapshot_id'], ['alpha.example.test'])


def test_encryption_failure_retains_private_source_without_live_changes(prepared, monkeypatch):
    account, _, plan, script = prepared
    before = script.read_bytes()
    def fail(*args, **kwargs): raise RuntimeError('simulated storage failure')
    monkeypatch.setattr(storage, 'backup', fail)
    with pytest.raises(RuntimeError, match='storage failure'):
        recovery.save_previous(None, account, 84, plan['previous'])
    source = jobs.private_directory('restores', 'restore-84') / 'mail-routing-recovery.json'
    assert source.stat().st_mode & 0o777 == 0o600
    assert json.loads(source.read_text())['restore_id'] == 84
    assert script.read_bytes() == before and routing.capture(account) == plan['previous']['routing']


def test_incomplete_script_safety_rejected_before_storage(prepared, monkeypatch):
    account, _, plan, _ = prepared
    bundle = deepcopy(plan['previous']); bundle['scripts']['scripts'] = []
    def forbidden(*args, **kwargs): raise AssertionError('No storage call expected')
    monkeypatch.setattr(storage, 'backup', forbidden)
    with pytest.raises(ValidationError, match='missing an automatic-reply script'):
        recovery.save_previous(None, account, 85, bundle)


def test_forwarding_only_bundle_preserves_unmanaged_custom_script(prepared, mail_database, tmp_path):
    account, _, _, script = prepared
    connection, _, _, _ = mail_database
    with connection.cursor() as cursor:
        cursor.execute("DELETE a FROM mail_autoresponder a JOIN mail_user u ON a.mail_user_id=u.id JOIN mail_domain d ON u.domain_id=d.id WHERE d.domain='alpha.example.test'")
    desired = routing.capture(account)
    desired['domains'][0]['forwards'] = []
    plan = recovery.prepare(account, desired)
    assert plan['desired_scripts']['scripts'] == [] and plan['previous']['scripts']['scripts'] == []
    repo = repository(tmp_path)
    result = recovery.save_previous(repo, account, 86, plan['previous'])
    assert recovery.load_previous(repo, account, result['snapshot_id'], 86) == plan['previous']
    assert script.read_bytes() == b'# custom previous script\r\nkeep;\r\n'
