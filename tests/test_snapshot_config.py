from pathlib import Path
import shutil

import pytest
from sqlalchemy import delete, select

from daemon import snapshot_config, snapshot_jobs as jobs, webhooks
from daemon.appcrypto import decrypt_env, encrypt_env
from shared.config import settings
from shared.db import write_session
from shared.models import (Account, AccountNotificationPrefs, BackupTelegramSettings,
    NotificationSettings, SnapshotDestination, SnapshotPolicy, SnapshotRun, Webhook)


@pytest.fixture
def environment(isolated_db,tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'snapshot_private_dir',str(tmp_path/'private'))
    monkeypatch.setattr(settings,'home_base',str(tmp_path/'home'))
    monkeypatch.setattr(jobs._executor,'submit',lambda *_:None)
    home=tmp_path/'home'/'alpha';home.mkdir(parents=True);(home/'site.txt').write_text('site')
    with write_session() as session:session.add(Account(username='alpha',status='active',uid=2000,gid=2000))
    return tmp_path,[]


def test_encrypted_configuration_preview_and_recovery(environment):
    root,_=environment
    destination=jobs.create_destination({'name':'Recoverable local','path':str(root/'repository')})
    jobs.initialize_destination({'id':destination['id']})
    policy=jobs.save_policy({'name':'Recovery job','destination_id':destination['id'],
        'accounts':['alpha'],'components':['files'],'mode':'incremental'})
    ident=jobs.queue_policy({'id':policy['id']})['run_ids'][0];jobs.execute_run(ident)
    password=jobs.recovery_key({'id':destination['id']})['password']
    exported=snapshot_config.export_configuration({})
    assert exported['summary']=={'destinations':1,'policies':1,'catalog_entries':1}
    assert password not in exported['payload']
    with pytest.raises(Exception,match='incorrect|corrupt'):
        snapshot_config.import_configuration({'payload':exported['payload'],'recovery_key':'wrong-key-value-long-enough'})
    preview=snapshot_config.import_configuration({'payload':exported['payload'],'recovery_key':exported['recovery_key']})
    assert preview['status']=='preview' and preview['conflicts']['destinations']==['Recoverable local']

    namespace=destination['namespace']
    with write_session() as session:
        session.execute(delete(SnapshotRun));session.execute(delete(SnapshotPolicy));session.execute(delete(SnapshotDestination))
    shutil.rmtree(Path(settings.snapshot_private_dir)/'repositories'/namespace)
    imported=snapshot_config.import_configuration({'payload':exported['payload'],
        'recovery_key':exported['recovery_key'],'apply':True})
    assert imported['status']=='imported' and imported['catalog_imported']==1
    with write_session() as session:
        restored=session.scalar(select(SnapshotDestination));restored_policy=session.scalar(select(SnapshotPolicy))
        assert restored.enabled is False and restored.status=='draft'
        assert restored_policy.enabled is False
    assert jobs.recovery_key({'id':restored.id})['password']==password


def test_notification_plugins_round_trip_disabled(environment):
    secret='configuration-recovery-secret'
    with write_session() as session:
        account=session.scalar(select(Account).where(Account.username=='alpha'))
        session.add(NotificationSettings(id=1,sender_address='backups@example.com',events={'backup.completed':True}))
        session.add(AccountNotificationPrefs(account_id=account.id,customer_email='owner@example.com',
            events={'backup.failed':True}))
        session.add(BackupTelegramSettings(id=1,enabled=True,chat_id='12345',events=['backup.failed'],
            token_enc=encrypt_env({'token':'telegram-token'})))
    webhooks.create_webhook({'url':'https://example.com/backup-hook','events':['backup.completed'],
        'secret':secret})

    exported=snapshot_config.export_configuration({})
    assert secret not in exported['payload']
    preview=snapshot_config.import_configuration({'payload':exported['payload'],
        'recovery_key':exported['recovery_key']})
    assert preview['notification_plugins']==3
    assert preview['conflicts']['webhooks']==['https://example.com/backup-hook']

    with write_session() as session:
        session.execute(delete(Webhook))
        session.execute(delete(AccountNotificationPrefs))
        session.execute(delete(BackupTelegramSettings))
        session.execute(delete(NotificationSettings))
    imported=snapshot_config.import_configuration({'payload':exported['payload'],
        'recovery_key':exported['recovery_key'],'apply':True})
    assert imported['status']=='imported'
    with write_session() as session:
        email=session.get(NotificationSettings,1)
        preference=session.scalar(select(AccountNotificationPrefs))
        telegram=session.get(BackupTelegramSettings,1)
        hook=session.scalar(select(Webhook))
        assert email.sender_address=='backups@example.com'
        assert preference.customer_email=='owner@example.com'
        assert telegram.enabled is False
        assert decrypt_env(telegram.token_enc)['token']=='telegram-token'
        assert hook.enabled is False
        assert hook.secret!=secret and webhooks.reveal_secret(hook.secret)==secret
