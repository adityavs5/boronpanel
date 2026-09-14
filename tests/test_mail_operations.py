"""Mail edits and recovery must share the existing SQL mutation lock."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading

import pytest

from daemon import (autoresponder, database_operations, handlers_email_routing,
                    handlers_mail, mail, snapshot_jobs, snapshot_mail_routing)
from shared.validation import ValidationError


@pytest.mark.parametrize('function,args', [
    (mail.create_mail_domain, ('alpha.test',)),
    (mail.delete_mail_domain, ('alpha.test',)),
    (mail.set_domain_active, ('alpha.test', True)),
    (mail.create_mailbox, ('alpha.test', 'inbox', 'test-only-password')),
    (mail.delete_mailbox, ('alpha.test', 'inbox')),
    (mail.change_mailbox_password, ('alpha.test', 'inbox', 'test-only-password')),
    (mail.create_forward, ('alpha.test', 'sales', 'inbox@example.test')),
    (mail.delete_forward, ('alpha.test', 'sales', 'inbox@example.test')),
    (mail.delete_all_forwards_for_domain, ('alpha.test',)),
    (mail.set_catchall, ('alpha.test', 'inbox@example.test')),
    (mail.delete_catchall, ('alpha.test',)),
    (mail.set_autoresponder, ('alpha.test', 'inbox', 'Away', 'Reply', None, None)),
    (mail.delete_autoresponder, ('alpha.test', 'inbox')),
    (autoresponder.apply_autoresponder, ('alpha.test', 'inbox', 'Away', 'Reply', None, None)),
    (autoresponder.remove_autoresponder, ('alpha.test', 'inbox')),
    *[(getattr(handlers_mail, name), ({},)) for name in (
        'create_mail_domain', 'delete_mail_domain', 'create_mailbox', 'delete_mailbox',
        'change_mailbox_password', 'create_forward', 'delete_forward', 'set_catchall',
        'delete_catchall', 'set_autoresponder', 'delete_autoresponder')],
    (handlers_mail.ensure_mail_domain, ('alpha.test',)),
    (handlers_mail._delete_mail_domain_cache, ('alpha.test',)),
    (handlers_mail.terminate_account_mail, (None,)),
    (handlers_email_routing.set_routing, ({},)),
])
def test_mail_mutations_stop_before_any_side_effect(function, args):
    # The other open file description simulates a competing process. Handler
    # dictionaries are intentionally empty: lock rejection precedes parsing,
    # cache writes, SQL queries, filesystem operations and Sieve compilation.
    with snapshot_jobs.lock('database-mutations', blocking=False):
        with pytest.raises(ValidationError, match='database backup or management'):
            function(*args)


def test_routing_capture_waits_and_resumes_after_sql_mutation(monkeypatch):
    started = threading.Event()
    read = threading.Event()
    def bindings(account):
        read.set()
        return []
    monkeypatch.setattr(snapshot_mail_routing, '_domain_bindings', bindings)
    def capture():
        started.set()
        return snapshot_mail_routing.capture(SimpleNamespace(username='alpha'))
    with ThreadPoolExecutor(max_workers=1) as executor:
        with database_operations.mutation_lock():
            future = executor.submit(capture)
            assert started.wait(2)
            assert not read.wait(.1)
        assert future.result(timeout=3) == dict(format=1, username='alpha', domains=[])
    assert read.is_set()


def test_routing_worker_can_nest_direct_mail_edits(isolated_db, monkeypatch):
    calls = []
    monkeypatch.setattr(mail, '_domain_id', lambda domain: 7)
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, params): calls.append((query, params))
    class Connection:
        def cursor(self): return Cursor()
        def close(self): pass
    monkeypatch.setattr(mail, '_connect', Connection)
    @database_operations.serialized_worker
    def restore():
        handlers_mail.delete_catchall({'domain': 'alpha.test'})
    restore()
    assert len(calls) == 1 and calls[0][1] == (7,)
    with snapshot_jobs.lock('database-mutations', blocking=False):
        pass


def test_mail_backup_looks_up_ownership_after_waiting(isolated_db, tmp_path, monkeypatch):
    from sqlalchemy import select
    from shared.db import write_session
    from shared.models import Account, MailDomain
    from daemon import snapshot_mail_metadata
    with write_session() as session:
        account = Account(username='alpha', status='active')
        other = Account(username='bravo', status='active')
        session.add_all([account, other]); session.flush()
        session.add(MailDomain(account_id=account.id, domain='alpha.example.test'))
    seen = []
    started = threading.Event()
    def capture(username, domains):
        # The source coordinator must retain the lock while capturing metadata.
        with pytest.raises(BlockingIOError):
            with snapshot_jobs.lock('database-mutations', blocking=False): pass
        seen.extend(domain.domain for domain in domains)
        return dict(format=1, username=username, domains=[])
    monkeypatch.setattr(snapshot_mail_metadata, 'capture', capture)
    stage = tmp_path / 'sources'; stage.mkdir(mode=0o700)
    def backup():
        started.set()
        return snapshot_jobs._mail_sources(account, stage)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with database_operations.mutation_lock():
            future = executor.submit(backup)
            assert started.wait(2)
            with write_session() as session:
                session.scalar(select(MailDomain).where(MailDomain.domain == 'alpha.example.test')).account_id = other.id
        assert future.result(timeout=5) == []
    assert seen == []
    assert (stage / 'mail-recovery.json').stat().st_mode & 0o077 == 0
