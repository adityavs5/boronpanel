import json
import logging
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from daemon import mail, snapshot_mail_metadata as metadata, snapshot_jobs as jobs, snapshot_storage as storage
from shared.config import settings
from shared.db import write_session
from shared.models import Account, MailDomain, SnapshotDestination, SnapshotRun
from tests.test_snapshot_databases import sql_server, sql


@pytest.fixture
def mail_database(sql):
    connection, work = sql
    script = (Path(__file__).parents[1]/'scripts/install.sh').read_text()
    start = script.index('CREATE TABLE IF NOT EXISTS mail_domain (')
    schema = script[start:script.index('\nSQL', start)]
    password = 'test-only-mail-recovery-password'
    hashed = mail.hash_password(password)
    with connection.cursor() as cursor:
        cursor.execute('DROP DATABASE IF EXISTS boron_mail')
        cursor.execute('CREATE DATABASE boron_mail')
        cursor.execute('USE boron_mail')
        for statement in schema.split(';'):
            if statement.strip():cursor.execute(statement)
        for domain in ('alpha.example.test','bravo.example.test'):
            cursor.execute('INSERT INTO mail_domain (domain,active) VALUES (%s,1)', (domain,))
            domain_id = cursor.lastrowid
            cursor.execute('INSERT INTO mail_user (domain_id,local_part,password,quota_mb,active) VALUES (%s,%s,%s,2048,1)', (domain_id,'inbox',hashed))
            user_id = cursor.lastrowid
            cursor.execute('INSERT INTO mail_forward (domain_id,source_local_part,destination,active) VALUES (%s,%s,%s,0)', (domain_id,'sales','target@example.test'))
            cursor.execute('INSERT INTO mail_catchall (domain_id,destination,active) VALUES (%s,%s,0)', (domain_id,'catch@example.test'))
            cursor.execute('INSERT INTO mail_autoresponder (mail_user_id,subject,body,start_date,end_date,active) VALUES (%s,%s,%s,%s,%s,0)',
                           (user_id,'Away','Test-only reply','2026-09-01','2026-09-30'))
    return connection,work,password,hashed


def captured():
    return metadata.capture('alpha',[SimpleNamespace(domain='alpha.example.test')])


def test_mail_metadata_preserves_settings_without_other_domains(mail_database, caplog):
    connection,work,password,hashed = mail_database
    caplog.set_level(logging.INFO)
    payload = captured()
    assert payload['username'] == 'alpha'
    assert len(payload['domains']) == 1
    domain = payload['domains'][0]
    assert domain['domain'] == 'alpha.example.test'
    assert domain['mailboxes'][0]['password_hash'] == hashed
    assert domain['mailboxes'][0]['quota_mb'] == 2048
    assert domain['forwards'][0]['active'] == 0
    assert domain['catchall']['destination'] == 'catch@example.test'
    assert domain['autoresponders'][0]['start_date'] == '2026-09-01'
    assert domain['autoresponders'][0]['body'] == 'Test-only reply'
    serialized = json.dumps(payload)
    assert 'bravo.example.test' not in serialized and password not in serialized
    assert password not in caplog.text and hashed not in caplog.text
    public = repr(mail.list_mailboxes('alpha.example.test'))
    assert hashed not in public and 'password' not in public


def test_missing_mailbox_recovers_original_dovecot_password_and_quota(mail_database):
    connection,work,password,hashed = mail_database
    mailbox = captured()['domains'][0]['mailboxes'][0]
    with connection.cursor() as cursor:
        cursor.execute("DELETE u FROM mail_user u JOIN mail_domain d ON u.domain_id=d.id WHERE d.domain='alpha.example.test'")
    result = metadata.recreate_mailbox('alpha.example.test',mailbox)
    assert result['quota_mb'] == 2048 and 'password_hash' not in result
    with connection.cursor() as cursor:
        cursor.execute("SELECT u.password,u.quota_mb,u.active FROM mail_user u JOIN mail_domain d ON u.domain_id=d.id WHERE d.domain='alpha.example.test'")
        restored_hash, quota, active = cursor.fetchone()
        assert (restored_hash, quota, active) == (hashed,2048,1)
        cursor.execute("SELECT COUNT(*) FROM mail_user u JOIN mail_domain d ON u.domain_id=d.id WHERE d.domain='bravo.example.test'")
        assert cursor.fetchone()[0] == 1
    # Synthetic fixture credentials only; bypass the command logger to avoid
    # logging hashes. No real mail account or message delivery is involved.
    result = subprocess.run(['doveadm','pw','-t',restored_hash],input=password+'\n'+password+'\n',text=True,capture_output=True,timeout=20)
    if result.returncode != 0 or '(verified)' not in result.stdout:
        raise AssertionError('Dovecot did not verify the recovered fixture password')


def test_existing_mailbox_is_not_replaced(mail_database):
    connection,work,password,hashed = mail_database
    mailbox = captured()['domains'][0]['mailboxes'][0]
    with connection.cursor() as cursor:
        cursor.execute("UPDATE mail_user u JOIN mail_domain d ON u.domain_id=d.id SET u.quota_mb=4096,u.active=0 WHERE d.domain='alpha.example.test'")
    with pytest.raises(Exception,match='already exists'):
        metadata.recreate_mailbox('alpha.example.test',mailbox)
    assert mail.list_mailboxes('alpha.example.test')[0]['quota_mb'] == 4096
    assert mail.list_mailboxes('alpha.example.test')[0]['active'] == 0


@pytest.mark.parametrize('change', [{'local_part':'../../escape'},{'password_hash':'{PLAIN}secret'},{'password_hash':'{ARGON2ID}bad\nline'},{'quota_mb':0},{'quota_mb':True},{'active':'yes'}])
def test_invalid_mailbox_metadata_is_rejected(change):
    entry = {'local_part':'inbox','password_hash':'{ARGON2ID}test-only-hash','quota_mb':1024,'active':True,**change}
    with pytest.raises(Exception):metadata.validate_mailbox(entry)


def test_encrypted_mail_job_contains_messages_and_private_recovery_metadata(mail_database,isolated_db,monkeypatch):
    connection,work,password,hashed = mail_database
    home = work/'homes'/'alpha';home.mkdir(parents=True)
    message = work/'mail'/'alpha.example.test'/'inbox'/'Maildir'/'cur'/'proof:2,S'
    message.parent.mkdir(parents=True)
    for folder in ('new', 'tmp'):
        (message.parent.parent / folder).mkdir()
    content = b'Subject: Recovery fixture\r\n\r\nOriginal offline message.\r\n'
    message.write_bytes(content)
    retained = message.parents[2]/('.boron-mail-ready-'+'a'*32)
    retained.mkdir()
    (retained/'old-message').write_bytes(b'retained recovery-only message')
    monkeypatch.setattr(settings,'home_base',str(home.parent))
    monkeypatch.setattr(settings,'mail_base',str(work/'mail'))
    monkeypatch.setattr(jobs._executor,'submit',lambda *args:None)
    with write_session() as session:
        account = Account(username='alpha',status='active',uid=65534,gid=65534)
        session.add(account);session.flush()
        session.add(MailDomain(account_id=account.id,domain='alpha.example.test'))
    destination = jobs.create_destination({'name':'Mail metadata','path':str(work/'repository')})
    jobs.initialize_destination({'id':destination['id']})
    policy = jobs.save_policy({'name':'Mail','destination_id':destination['id'],'accounts':['alpha'],'components':['mail']})
    run_id = jobs.queue_policy({'id':policy['id']})['run_ids'][0]
    jobs.execute_run(run_id)
    run = jobs._row(SnapshotRun,run_id)
    assert run.status == 'completed',run.error
    repo = jobs.repository(jobs._row(SnapshotDestination,destination['id']))
    data = storage.restore_to(repo,account.id,run.snapshot_id,str(work/'restored'))
    assert (data/str(message).lstrip('/')).read_bytes() == content
    assert not (data/str(retained).lstrip('/')).exists()
    safety = storage.backup(repo, account.id, [str(retained)])
    safety_data = storage.restore_to(repo, account.id, safety['snapshot_id'], str(work/'safety-restored'))
    assert (safety_data/str(retained).lstrip('/')/'old-message').read_bytes() == b'retained recovery-only message'
    documents = list(data.rglob('mail-recovery.json'))
    assert len(documents) == 1
    assert documents[0].stat().st_mode & 0o777 == 0o600
    restored = json.loads(documents[0].read_text())
    assert restored['domains'][0]['mailboxes'][0]['password_hash'] == hashed
    assert password not in documents[0].read_text()
    assert 'bravo.example.test' not in documents[0].read_text()
    from daemon import snapshot_restores as restores
    from shared.validation import ValidationError
    options = restores.mailbox_options({'username': 'alpha', 'run_id': run_id})
    assert options['mailboxes'] == [{'address': 'inbox@alpha.example.test',
        'domain': 'alpha.example.test', 'local_part': 'inbox', 'quota_mb': 2048,
        'active': True, 'available': True, 'action': 'existing', 'reason': None}]
    assert hashed not in repr(options) and password not in repr(options)
    from daemon import snapshot_mail_restore as restore_mail
    import shutil
    prepared = restore_mail.prepare(account, repo, run.snapshot_id, ['inbox@alpha.example.test'])
    try:
        entry = prepared['entries'][0]
        assert entry['action'] == 'existing'
        files = list((Path(entry['prepared']) / 'cur').iterdir())
        assert len(files) == 1 and files[0].read_bytes() == content
        assert ':2,S' in files[0].name
        assert message.read_bytes() == content
        assert entry['metadata']['password_hash'] == hashed
    finally:
        shutil.rmtree(prepared['work'])
    with connection.cursor() as cursor:
        cursor.execute("DELETE u FROM mail_user u JOIN mail_domain d ON u.domain_id=d.id WHERE d.domain='alpha.example.test'")
    assert restores.mailbox_options({'username': 'alpha', 'run_id': run_id})['mailboxes'][0]['action'] == 'recreate'
    with write_session() as session:
        session.add(Account(username='bravo', status='active', uid=65533, gid=65533))
    def forbidden(*args, **kwargs):
        raise AssertionError('Foreign snapshot must be rejected before decryption')
    monkeypatch.setattr(storage, 'restore_to', forbidden)
    with pytest.raises(ValidationError):
        restores.mailbox_options({'username': 'bravo', 'run_id': run_id})
