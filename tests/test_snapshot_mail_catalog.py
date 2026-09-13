import copy
import json
from types import SimpleNamespace

import pytest

from daemon import mail, snapshot_mail_metadata as metadata, snapshot_restores as restores
from shared.db import write_session
from shared.models import Account, MailDomain
from shared.validation import ValidationError


@pytest.fixture
def saved(tmp_path):
    mailbox = {'local_part': 'inbox', 'password_hash': '{ARGON2ID}synthetic-fixture-hash', 'quota_mb': 1024, 'active': True}
    payload = {'format': 1, 'username': 'alpha', 'domains': [
        {'domain': 'alpha.example.test', 'active': True, 'mailboxes': [mailbox], 'forwards': []}]}
    path = tmp_path / 'mail-recovery.json'
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    return path, payload


def test_private_reader_returns_only_validated_mailbox_metadata(saved):
    path, payload = saved
    result = metadata.read_mailboxes(path, 'alpha')
    assert result['alpha.example.test']['mailboxes']['inbox']['quota_mb'] == 1024
    assert 'forwards' not in result['alpha.example.test']


@pytest.mark.parametrize('bad', ['owner', 'duplicate-domain', 'duplicate-mailbox', 'hash', 'status', 'format'])
def test_malformed_or_wrong_account_metadata_rejected(saved, bad):
    path, original = saved
    payload = copy.deepcopy(original)
    if bad == 'owner': payload['username'] = 'bravo'
    elif bad == 'duplicate-domain': payload['domains'].append(payload['domains'][0])
    elif bad == 'duplicate-mailbox': payload['domains'][0]['mailboxes'] *= 2
    elif bad == 'hash': payload['domains'][0]['mailboxes'][0]['password_hash'] = '{PLAIN}private-marker'
    elif bad == 'status': payload['domains'][0]['active'] = 'yes'
    else: payload['format'] = True
    path.write_text(json.dumps(payload))
    with pytest.raises(ValidationError) as error:
        metadata.read_mailboxes(path, 'alpha')
    assert 'private-marker' not in str(error.value)


def test_public_and_symlink_metadata_rejected(saved):
    path, payload = saved
    path.chmod(0o644)
    with pytest.raises(ValidationError): metadata.read_mailboxes(path, 'alpha')
    path.chmod(0o600)
    link = path.with_name('link')
    link.symlink_to(path)
    with pytest.raises(ValidationError): metadata.read_mailboxes(link, 'alpha')


@pytest.mark.parametrize('owner', ['alpha', 'bravo', 'missing'])
def test_catalog_checks_live_domain_ownership_and_never_exposes_hashes(saved, isolated_db, monkeypatch, owner):
    path, payload = saved
    with write_session() as session:
        alpha = Account(username='alpha', status='active', uid=65534, gid=65534)
        bravo = Account(username='bravo', status='active', uid=65533, gid=65533)
        session.add_all([alpha, bravo]); session.flush()
        if owner != 'missing':
            session.add(MailDomain(account_id=alpha.id if owner == 'alpha' else bravo.id, domain='alpha.example.test'))
    source = SimpleNamespace(options={'components': ['mail']}, destination_id=1, snapshot_id='fixture')
    monkeypatch.setattr(restores, '_owned_run', lambda *args: (alpha, source))
    monkeypatch.setattr(restores.jobs, '_row', lambda *args: object())
    monkeypatch.setattr(restores.jobs, 'repository', lambda *args: object())
    monkeypatch.setattr(restores, '_mail_recovery_metadata', lambda *args: metadata.read_mailboxes(path, 'alpha'))
    lookups = []
    def listed(domain):
        lookups.append(domain)
        return [{'local_part': 'inbox'}]
    monkeypatch.setattr(mail, 'list_mailboxes', listed)
    result = restores.mailbox_options({'username': 'alpha', 'run_id': 1})
    row = result['mailboxes'][0]
    assert row['available'] is (owner == 'alpha')
    assert row['action'] == ('existing' if owner == 'alpha' else 'unavailable')
    assert lookups == (['alpha.example.test'] if owner == 'alpha' else [])
    assert 'password_hash' not in repr(result) and 'synthetic-fixture-hash' not in repr(result)
    if owner == 'alpha':
        monkeypatch.setattr(mail, 'list_mailboxes', lambda domain: [])
        assert restores.mailbox_options({'username': 'alpha', 'run_id': 1})['mailboxes'][0]['action'] == 'recreate'
