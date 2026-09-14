import pytest
from sqlalchemy import select

from daemon import mail_mutation, mail, handlers_mail, autoresponder, handlers_account
from daemon import handlers_email_routing, snapshot_jobs as jobs
from shared.db import write_session
from shared.models import Account, Domain, MailDomain, SnapshotRestore, SnapshotRun
from shared.validation import ValidationError
from tests.test_snapshot_routing_retention import retention


@pytest.fixture
def recovering(retention):
    create, _, _, _ = retention
    ident = create(status='failed', finalized=False)
    with write_session() as session:
        restore = session.get(SnapshotRestore, ident)
        session.get(SnapshotRun, restore.run_id).status = 'completed'
        account = session.scalar(select(Account).where(Account.username == 'alpha'))
        other = Account(username='bravo', status='active'); session.add(other); session.flush()
        session.add(MailDomain(account_id=account.id, domain='alpha.example.test'))
        session.add(MailDomain(account_id=other.id, domain='bravo.example.test'))
        session.add(Domain(account_id=account.id, domain='lazy.example.test', kind='addon', docroot='/tmp/test-only'))
    return ident, account, other


@pytest.mark.parametrize('invoke', [
    lambda: mail.create_forward('alpha.example.test', 'sales', 'target@example.test'),
    lambda: mail.delete_mail_domain(domain='alpha.example.test'),
    lambda: mail.change_mailbox_password('alpha.example.test', 'inbox', 'test-only'),
    lambda: handlers_mail.delete_mail_domain({'domain': 'alpha.example.test'}),
    lambda: handlers_mail.create_mailbox({'domain': 'lazy.example.test', 'local_part': 'new', 'password': 'test-only'}),
    lambda: handlers_mail.create_mail_domain({'domain': 'new.example.test', 'username': 'alpha'}),
    lambda: autoresponder.remove_autoresponder('alpha.example.test', 'inbox'),
    lambda: handlers_email_routing.set_routing({'username': 'alpha', 'domain': 'alpha.example.test', 'mode': 'remote'}),
])
def test_persisted_failed_recovery_blocks_edits_without_worker_lock(recovering, invoke, monkeypatch):
    monkeypatch.setattr(mail, '_connect', lambda: pytest.fail('Must reject before provider SQL'))
    with pytest.raises(ValidationError, match='needs recovery'): invoke()


@pytest.mark.parametrize('status', ['pending', 'running', 'completed'])
def test_pending_running_or_unfinalized_completed_job_blocks(recovering, status):
    ident, _, _ = recovering
    with write_session() as session: session.get(SnapshotRestore, ident).status = status
    @mail_mutation.serialized
    def change(domain): pytest.fail('Mutation must not run')
    with pytest.raises(ValidationError, match='needs recovery'): change('alpha.example.test')


def test_other_account_and_read_only_listing_remain_available(recovering, monkeypatch):
    _, _, other = recovering
    @mail_mutation.serialized
    def change(domain): return domain
    assert change('bravo.example.test') == 'bravo.example.test'
    monkeypatch.setattr(mail, 'list_mailboxes', lambda domain: [{'local_part': 'inbox'}])
    assert handlers_mail.list_mailboxes({'domain': 'alpha.example.test'})['mailboxes'] == [{'local_part': 'inbox'}]


def test_finalized_rollback_allows_edits_with_safety_retained(recovering):
    ident, _, _ = recovering
    with write_session() as session:
        row = session.get(SnapshotRestore, ident)
        row.summary = {**row.summary, 'routing_finalized': True}
    @mail_mutation.serialized
    def change(domain): return 'changed'
    assert change('alpha.example.test') == 'changed'
    assert jobs._row(SnapshotRestore, ident).safety_snapshot_id


def test_failure_before_any_safety_copy_does_not_lock_account(recovering):
    ident, _, _ = recovering
    with write_session() as session:
        row = session.get(SnapshotRestore, ident); row.safety_snapshot_id = None; row.summary = {}
    @mail_mutation.serialized
    def change(domain): return 'changed'
    assert change('alpha.example.test') == 'changed'


@pytest.mark.parametrize('action', [handlers_account.suspend_account, handlers_account.terminate_account])
def test_lifecycle_refuses_before_account_or_service_changes(recovering, action, monkeypatch):
    _, account, _ = recovering
    monkeypatch.setattr(handlers_account.sysops, 'lock_user', lambda *a: pytest.fail('No system user mutation'))
    with pytest.raises(ValidationError, match='needs recovery'): action({'username': 'alpha'})
    assert jobs._row(Account, account.id).status == 'active'
