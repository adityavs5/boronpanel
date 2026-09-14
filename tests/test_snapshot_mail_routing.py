from copy import deepcopy
import pytest
from daemon import snapshot_mail_routing as routing, mail
from shared.db import write_session
from shared.models import Account, MailDomain
from shared.validation import ValidationError


@pytest.fixture
def saved_routing(isolated_db,monkeypatch):
    with write_session() as session:
        account=Account(username='alpha',status='active');foreign=Account(username='bravo',status='active')
        session.add_all([account,foreign]);session.flush()
        session.add_all([MailDomain(account_id=account.id,domain='alpha.test'),MailDomain(account_id=foreign.id,domain='bravo.test')])
    monkeypatch.setattr(mail,'list_mailboxes',lambda domain:[dict(local_part='inbox')])
    payload=dict(format=1,username='alpha',domains=[dict(domain='alpha.test',active=True,
        mailboxes=[dict(local_part='inbox',password_hash='private-credential-marker',quota_mb=100)],
        forwards=[dict(source_local_part='sales',destination='inbox@alpha.test',active=1)],
        catchall=dict(destination='catch@example.test',active=0),
        autoresponders=[dict(local_part='inbox',subject='Away',body='Back soon\n.',start_date='2026-10-01',end_date='2026-10-05',active=1)])])
    return account,payload


def test_routing_validation_preserves_rules_and_excludes_credentials(saved_routing):
    account,payload=saved_routing;original=deepcopy(payload)
    result=routing.validate_for_restore(account,payload)
    domain=result['domains'][0]
    assert domain['forwards'][0]['active'] is True
    assert domain['catchall']['active'] is False
    assert domain['autoresponders'][0]['body']=='Back soon\n.'
    assert 'mailboxes' not in domain and 'active' not in domain
    assert 'private-credential-marker' not in repr(result)
    assert payload==original


@pytest.mark.parametrize('kind,field,value',[
    ('forwards','destination','bad address'),('forwards','source_local_part','../bad'),('forwards','active',2),
    ('autoresponders','local_part','missing'),('autoresponders','subject','Bad\r\nSubject'),
    ('autoresponders','body',''),('autoresponders','end_date','2026-09-01'),
    ('autoresponders','start_date','2026-02-30'),('autoresponders','active','yes'),
])
def test_routing_rejects_invalid_rules(saved_routing,kind,field,value):
    account,payload=saved_routing;payload['domains'][0][kind][0][field]=value
    with pytest.raises(ValidationError):routing.validate_for_restore(account,payload)


def test_routing_rejects_foreign_identity_domain_and_duplicates(saved_routing):
    account,payload=saved_routing
    with pytest.raises(ValidationError):routing.validate_for_restore(account,{**payload,'username':'bravo'})
    with pytest.raises(ValidationError):routing.validate_for_restore(account,{**payload,'format':True})
    with pytest.raises(ValidationError):routing.validate_for_restore(account,payload,['alpha.test','alpha.test'])
    payload['domains'].append({**payload['domains'][0],'domain':'bravo.test'})
    assert len(routing.validate_for_restore(account,payload,['alpha.test'])['domains'])==1
    with pytest.raises(ValidationError,match='no longer owned'):routing.validate_for_restore(account,payload)


def test_routing_requires_complete_metadata(saved_routing):
    account,payload=saved_routing
    del payload['domains'][0]['catchall']
    with pytest.raises(ValidationError,match='incomplete'):routing.validate_for_restore(account,payload)


def test_private_routing_reader_excludes_credentials_and_rejects_public_or_linked_files(saved_routing,tmp_path):
    import json
    account,payload=saved_routing
    path=tmp_path/'mail-recovery.json';path.write_text(json.dumps(payload));path.chmod(0o600)
    assert 'private-credential-marker' not in repr(routing.read_routing(path,account))
    path.chmod(0o644)
    with pytest.raises(ValidationError):routing.read_routing(path,account)
    path.chmod(0o600)
    link=tmp_path/'linked.json';link.symlink_to(path)
    with pytest.raises(ValidationError):routing.read_routing(link,account)
    path.write_text('invalid JSON')
    with pytest.raises(ValidationError):routing.read_routing(path,account)
