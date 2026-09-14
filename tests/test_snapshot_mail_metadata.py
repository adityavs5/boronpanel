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


@pytest.mark.parametrize('missing', [False, True])
def test_guarded_provisioning_recreates_missing_users_and_updates_cache(mail_database, isolated_db, monkeypatch, missing):
    from daemon import snapshot_mail_restore as restore, snapshot_mail_guard as guard
    from shared.models import MailUser
    connection, work, password, hashed = mail_database
    saved_mailbox = captured()['domains'][0]['mailboxes'][0]
    private = work / 'private'
    private.mkdir(mode=0o700)
    preparation = private / 'prepared'
    preparation.mkdir(mode=0o700)
    base = work / 'mail'
    base.mkdir()
    monkeypatch.setattr(settings, 'snapshot_private_dir', str(private))
    monkeypatch.setattr(settings, 'mail_base', str(base))
    monkeypatch.setattr(settings, 'mail_restore_guard_dir', str(work / 'guards'))
    with write_session() as session:
        account = Account(username='alpha', status='active', uid=65534, gid=65534)
        session.add(account); session.flush()
        session.add(MailDomain(account_id=account.id, domain='alpha.example.test'))
    with connection.cursor() as cursor:
        if missing:
            cursor.execute("DELETE u FROM mail_user u JOIN mail_domain d ON u.domain_id=d.id WHERE d.domain='alpha.example.test'")
        else:
            cursor.execute("UPDATE mail_user u JOIN mail_domain d ON u.domain_id=d.id SET u.quota_mb=4096,u.active=0 WHERE d.domain='alpha.example.test'")
    prepared = {'work': str(preparation), 'entries': [dict(domain='alpha.example.test', local_part='inbox', metadata=saved_mailbox)]}
    acquired = restore.acquire_guards(account, prepared, 19)
    result = restore.provision_mailboxes(account, prepared, acquired, 19)
    assert result['mailboxes'][0]['action'] == ('recreate' if missing else 'existing')
    expected_quota = 2048 if missing else 4096
    actual = mail.list_mailboxes('alpha.example.test')[0]
    assert actual['quota_mb'] == expected_quota and actual['active'] == (1 if missing else 0)
    with write_session() as session:
        cached = session.scalar(select(MailUser).where(MailUser.domain == 'alpha.example.test'))
        assert cached.quota_mb == expected_quota
    with connection.cursor() as cursor:
        cursor.execute("SELECT u.password FROM mail_user u JOIN mail_domain d ON u.domain_id=d.id WHERE d.domain='alpha.example.test'")
        assert cursor.fetchone()[0] == hashed
    assert (base / 'alpha.example.test/inbox/Maildir/cur').is_dir()
    assert json.loads((preparation / 'provisioning.json').read_text())['status'] == 'completed'
    with guard.owned_guards(acquired['entries'], 19):
        pass
    with pytest.raises(FileExistsError):
        restore.provision_mailboxes(account, prepared, acquired, 19)


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
    # Exercise the assembled workflow with real SQL/restic/offline Dovecot and
    # temporary Maildirs. Supervision is replaced only for this isolated tree;
    # real systemd shutdown/restart is covered by the journal/service suites.
    from daemon import snapshot_mail_journal as journal, snapshot_mail_service as supervisor
    from daemon import snapshot_mail_guard_config as guard_config
    import os
    monkeypatch.setattr(settings, 'mail_restore_guard_dir', str(work / 'workflow-guards'))
    monkeypatch.setattr(guard_config, 'verify', lambda: {'guard': 'ready'})
    monkeypatch.setattr(journal, 'require_stopped', lambda *args: None)
    monkeypatch.setattr(journal, 'launch', lambda path: journal.execute(path))
    monkeypatch.setattr(supervisor, 'inspect_switch', lambda *a, **kw: {'state': 'missing'})
    monkeypatch.setattr(supervisor, 'service_status', lambda *a: {
        'LoadState': 'loaded', 'ActiveState': 'active', 'SubState': 'running', 'ControlPID': '0'})
    for directory in (message.parents[3], message.parents[2], message.parents[1],
                      *[message.parents[1] / name for name in ('cur', 'new', 'tmp')]):
        os.chown(directory, 150, 150)
        directory.chmod(0o700)
    message.write_bytes(b'mail received after recovery point')
    checkpoints = []
    from shared.models import SnapshotRestore, SnapshotMailRecovery
    with write_session() as session:
        session.add(SnapshotRestore(id=29, run_id=run.id, account_id=account.id,
                                    selection={'kind': 'mail', 'mailboxes': ['inbox@alpha.example.test']}, status='running'))
    with pytest.raises(ValidationError, match='out of sequence'):
        restores._mail_checkpoint(29, 'completed', {})
    original_checkpoint = restores._mail_checkpoint
    def checkpoint(ident, phase, state):
        original_checkpoint(ident, phase, state)
        checkpoints.append((phase, state))
    monkeypatch.setattr(restores, '_mail_checkpoint', checkpoint)
    with write_session() as session:
        session.get(SnapshotRestore, 29).status = 'pending'
    restores.execute(29)
    with write_session() as session:
        finished = session.get(SnapshotRestore, 29)
        assert finished.status == 'completed', finished.error
        result = dict(finished.summary, safety_snapshot_id=finished.safety_snapshot_id)
    assert result['guards_released'] and result['mailboxes'] == 1
    assert [p for p, state in checkpoints] == ['preparing', 'prepared', 'guarded', 'provisioned', 'staged',
                                               'switching', 'switched', 'safety_saved', 'completed']
    assert hashed not in repr(checkpoints) and password not in repr(checkpoints)
    with write_session() as session:
        saved_job = session.get(SnapshotRestore, 29)
        saved_checkpoint = session.get(SnapshotMailRecovery, 29)
        assert saved_job.status == 'completed' and saved_job.safety_snapshot_id == result['safety_snapshot_id']
        assert saved_checkpoint.phase == 'completed'
        assert saved_checkpoint.work == checkpoints[1][1]['work']
        assert saved_checkpoint.work not in repr(restores._serialize(saved_job))
        # Simulate a lost final job-state commit after guard release. The saved
        # release intent and safety receipt must finish recovery without replay.
        saved_job.status = 'running'
        saved_job.completed_at = None
        saved_job.error = 'interrupted acknowledgement'
        saved_checkpoint.phase = 'safety_saved'
    monkeypatch.setattr(journal, 'launch', lambda *a: pytest.fail('Recovery must never replay the switch'))
    restores.recover_mail_restore(29)
    with write_session() as session:
        recovered_job = session.get(SnapshotRestore, 29)
        assert recovered_job.status == 'completed' and recovered_job.error is None
        assert session.get(SnapshotMailRecovery, 29).phase == 'completed'
    live_messages = list(message.parent.iterdir())
    assert len(live_messages) == 1 and live_messages[0].read_bytes() == content
    assert not list(Path(settings.mail_restore_guard_dir).iterdir())
    safety_mail = storage.restore_to(repo, account.id, result['safety_snapshot_id'], str(work / 'workflow-safety'))
    old_messages = list(safety_mail.rglob('proof:2,S'))
    assert len(old_messages) == 1 and old_messages[0].read_bytes() == b'mail received after recovery point'
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
