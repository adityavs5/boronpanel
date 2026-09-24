import pytest
from sqlalchemy import select

from daemon import handlers_account, handlers_domain, handlers_mail, identity_admin, parked
from daemon.rpc_authority import AuthorizationError, Principal, authorize
from shared.config import settings
from shared.db import write_session
from shared.models import Account, DnsZone, Domain, MailDomain
from shared.validation import ValidationError


@pytest.fixture
def owners(isolated_db):
    with write_session() as db:
        alice, bob = Account(username='alice', status='active'), Account(username='bob', status='active')
        db.add_all([alice, bob]); db.flush()
        db.add(Domain(account_id=alice.id, domain='alice.example', docroot='/home/alice/public_html', kind='primary'))
        return alice.id, bob.id


@pytest.mark.parametrize('route', ['addon', 'parked', 'primary', 'account'])
@pytest.mark.parametrize('resource', ['mail', 'dns', 'service'])
def test_all_domain_claim_paths_reject_retained_foreign_resources(owners, route, resource, monkeypatch):
    alice, bob = owners
    domain = 'retained.example'
    with write_session() as db:
        if resource == 'mail':
            db.add(MailDomain(domain=domain, account_id=bob))
        elif resource == 'dns':
            db.add(DnsZone(zone=domain, account_id=bob))
        else:
            monkeypatch.setattr(settings, 'pma_hostname', domain)
    effects = []
    monkeypatch.setattr(handlers_account.sysops, 'create_linux_user', lambda *a: effects.append('linux'))
    monkeypatch.setattr(handlers_domain, 'ensure_docroot', lambda *a: effects.append('files'))
    params = {'username': 'alice', 'domain': domain}
    with pytest.raises(ValidationError):
        if route == 'addon':
            handlers_domain.add_domain(params)
        elif route == 'parked':
            parked.add_parked_domain({'username': 'alice', 'parked_domain': domain})
        elif route == 'primary':
            identity_admin.set_primary_domain(params)
        else:
            handlers_account.create_account({'username': 'charlie', 'primary_domain': domain})
    assert effects == []
    with write_session() as db:
        assert db.scalar(select(Domain).where(Domain.domain == domain)) is None


def test_same_owner_can_reclaim_web_domain_with_retained_mail(owners, monkeypatch):
    alice, _ = owners
    with write_session() as db:
        db.add(MailDomain(domain='retained.example', account_id=alice))
    monkeypatch.setattr(handlers_domain, 'ensure_docroot', lambda *a: None)
    monkeypatch.setattr(handlers_domain.ols, 'provision_vhost', lambda *a: None)
    result = handlers_domain.add_domain({'username': 'alice', 'domain': 'retained.example'})
    assert result['account_id'] == alice


@pytest.mark.parametrize('operation', ['mail.list_mailboxes', 'mail.change_password', 'mail.delete_mailbox',
                                     'mail.forward.create', 'mail.autoresponder.set', 'spamfilter.entries.list'])
def test_legacy_mixed_web_mail_ownership_denied_by_root(owners, operation):
    alice, bob = owners
    with write_session() as db:
        db.add(MailDomain(domain='alice.example', account_id=bob))
    principal = Principal('customer', 'alice-login', alice, 1, 'session')
    with pytest.raises(AuthorizationError, match='inconsistent'):
        authorize(operation, {'username': 'alice', 'domain': 'alice.example'}, principal)
    with pytest.raises(ValidationError, match='ownership must match'):
        handlers_mail.ensure_mail_domain('alice.example')


def test_orphaned_external_mail_cannot_be_adopted_by_new_web_owner(owners, monkeypatch):
    monkeypatch.setattr(handlers_mail.mail, 'domain_exists', lambda domain: True)
    with pytest.raises(ValidationError, match='administrator recovery required'):
        handlers_mail.ensure_mail_domain('alice.example')
    with write_session() as db:
        assert db.scalar(select(MailDomain)) is None


def test_mail_create_rejects_mismatched_host_owner(owners, monkeypatch):
    effects = []
    monkeypatch.setattr(handlers_mail.mail, 'create_mail_domain', lambda *a: effects.append(True))
    with pytest.raises(ValidationError, match='ownership must match'):
        handlers_mail.create_mail_domain({'username': 'bob', 'domain': 'alice.example'})
    assert effects == []
