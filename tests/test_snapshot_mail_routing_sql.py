import pytest
from sqlalchemy import select
from tests.test_snapshot_databases import sql_server, sql
from tests.test_snapshot_mail_metadata import mail_database
from daemon import mail, snapshot_mail_routing as routing
from shared.db import write_session
from shared.models import Account,MailDomain
from shared.validation import ValidationError


@pytest.fixture
def routing_account(mail_database,isolated_db):
    with write_session() as session:
        account=Account(username='alpha',status='active');foreign=Account(username='bravo',status='active')
        session.add_all([account,foreign]);session.flush()
        session.add_all([MailDomain(account_id=account.id,domain='alpha.example.test'),MailDomain(account_id=foreign.id,domain='bravo.example.test')])
    return account,foreign


def test_real_routing_capture_preserves_rules_without_credentials(routing_account,mail_database):
    account,_=routing_account
    _,_,password,hashed=mail_database
    result=routing.capture(account)
    assert len(result['domains'])==1
    domain=result['domains'][0]
    assert domain['domain']=='alpha.example.test'
    assert domain['forwards']==[dict(source_local_part='sales',destination='target@example.test',active=False)]
    assert domain['catchall']==dict(destination='catch@example.test',active=False)
    assert domain['autoresponders'][0]['start_date']=='2026-09-01'
    assert domain['autoresponders'][0]['active'] is False
    assert password not in repr(result) and hashed not in repr(result) and 'mailboxes' not in domain
    assert routing.validate_for_restore(account,result)==result


def test_real_routing_capture_uses_consistent_snapshot_during_edit(routing_account,mail_database,monkeypatch):
    account,_=routing_account
    writer,_,_,_=mail_database
    connect=mail._connect
    changed=False
    queries=[]
    class Cursor:
        def __init__(self,cursor):self.cursor=cursor
        def __enter__(self):self.cursor.__enter__();return self
        def __exit__(self,*args):return self.cursor.__exit__(*args)
        def __getattr__(self,name):return getattr(self.cursor,name)
        def execute(self,query,*args):
            nonlocal changed
            queries.append(query)
            result=self.cursor.execute(query,*args)
            if query.startswith('SELECT source_local_part') and not changed:
                changed=True
                with writer.cursor() as cursor:
                    cursor.execute("UPDATE mail_catchall SET destination='new@example.test' WHERE domain_id=(SELECT id FROM mail_domain WHERE domain='alpha.example.test')")
            return result
    class Connection:
        def __init__(self):self.connection=connect()
        def __getattr__(self,name):return getattr(self.connection,name)
        def cursor(self):return Cursor(self.connection.cursor())
    monkeypatch.setattr(mail,'_connect',Connection)
    first=routing.capture(account)
    assert changed and first['domains'][0]['catchall']['destination']=='catch@example.test'
    assert routing.capture(account)['domains'][0]['catchall']['destination']=='new@example.test'
    assert all('password' not in query.lower() and 'quota' not in query.lower() for query in queries)


def test_routing_capture_rejects_foreign_selection_before_sql(routing_account,monkeypatch):
    account,_=routing_account
    def unexpected():raise AssertionError('Must not open SQL for an unowned domain')
    monkeypatch.setattr(mail,'_connect',unexpected)
    with pytest.raises(ValidationError,match='selection'):
        routing.capture(account,['bravo.example.test'])
    assert routing.capture(account,[])==dict(format=1,username='alpha',domains=[])


def test_routing_capture_rejects_ownership_transfer_during_provider_read(routing_account,monkeypatch):
    account,foreign=routing_account
    connect=mail._connect
    def transfer_then_connect():
        with write_session() as session:
            session.scalar(select(MailDomain).where(MailDomain.domain=='alpha.example.test')).account_id=foreign.id
        return connect()
    monkeypatch.setattr(mail,'_connect',transfer_then_connect)
    with pytest.raises(ValidationError,match='ownership changed'):
        routing.capture(account)


def test_routing_sql_replacement_round_trip_preserves_mailbox_credentials(routing_account,mail_database):
    from copy import deepcopy
    account,foreign=routing_account
    connection,_,_,_=mail_database
    original=routing.capture(account)
    unrelated=routing.capture(foreign)
    with connection.cursor() as cursor:
        cursor.execute('SELECT id,password,quota_mb,active FROM mail_user ORDER BY id')
        mailbox_before=cursor.fetchall()
    desired=deepcopy(original)
    domain=desired['domains'][0]
    domain['forwards']=[dict(source_local_part='help',destination='inbox@alpha.example.test',active=True)]
    domain['catchall']=None
    domain['autoresponders'][0].update(subject='Changed reply',body='Restored body',active=True,start_date=None,end_date=None)
    assert routing._replace_sql(account,desired)==['alpha.example.test']
    assert routing.capture(account)==desired and routing.capture(foreign)==unrelated
    with connection.cursor() as cursor:
        cursor.execute('SELECT id,password,quota_mb,active FROM mail_user ORDER BY id')
        assert cursor.fetchall()==mailbox_before
    routing._replace_sql(account,original)
    assert routing.capture(account)==original


def test_routing_sql_failure_rolls_back_prior_table_changes(routing_account,monkeypatch):
    from copy import deepcopy
    account,_=routing_account
    original=routing.capture(account)
    desired=deepcopy(original)
    desired['domains'][0]['forwards']=[]
    connect=mail._connect
    class Cursor:
        def __init__(self,cursor):self.cursor=cursor
        def __enter__(self):self.cursor.__enter__();return self
        def __exit__(self,*args):return self.cursor.__exit__(*args)
        def __getattr__(self,name):return getattr(self.cursor,name)
        def execute(self,query,*args):
            if query.startswith('DELETE FROM mail_catchall'):
                raise RuntimeError('simulated failure after forwarder deletion')
            return self.cursor.execute(query,*args)
    class Connection:
        def __init__(self):self.connection=connect()
        def __getattr__(self,name):return getattr(self.connection,name)
        def cursor(self):return Cursor(self.connection.cursor())
    monkeypatch.setattr(mail,'_connect',Connection)
    with pytest.raises(RuntimeError,match='simulated failure'):
        routing._replace_sql(account,desired)
    assert routing.capture(account)==original
