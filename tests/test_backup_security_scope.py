import io
import tarfile
import types
import pytest
from sqlalchemy import select
from daemon import backup
from shared.db import write_session
from shared.models import Account, BackupDestination, BackupJob, DatabaseGrant, MailDomain, MailUser, RestoreJob


@pytest.fixture()
def resources(isolated_db,monkeypatch):
    with write_session() as s:
        owner=Account(username='demo1',status='active',uid=5001,gid=5001)
        victim=Account(username='victim1',status='active',uid=5002,gid=5002)
        s.add_all([owner,victim]);s.flush()
        dest=BackupDestination(name='local',kind='local',local_path='/tmp/test-backups')
        s.add(dest);s.flush()
        s.add(DatabaseGrant(account_id=victim.id,db_name='victim1_db',db_user='victim1_db'))
        domain=MailDomain(account_id=victim.id,domain='victim.example');s.add(domain);s.flush()
        s.add(MailUser(mail_domain_id=domain.id,local_part='alice',domain=domain.domain))
        job=BackupJob(account_id=owner.id,destination_id=dest.id,kind='full',status='completed',trigger='manual')
        s.add(job);s.flush();result=(dest.id,job.id)
    monkeypatch.setattr(backup._executor,'submit',lambda *a:pytest.fail('unauthorized job enqueued'))
    return result


@pytest.mark.parametrize('kind,item',[('database','victim1_db'),('mailbox','alice@victim.example'),('file','../victim1/private'),('file','--checkpoint-action=exec=touch canary')])
def test_backup_and_restore_reject_foreign_or_option_targets(resources,kind,item):
    destination,job=resources
    with pytest.raises(backup.BackupError):
        backup.trigger_backup({'username':'demo1','destination_id':destination,'kind':kind,'item_ref':item})
    with pytest.raises(backup.BackupError):
        backup.trigger_restore({'username':'demo1','backup_job_id':job,'kind':kind,'item_ref':item})
    with write_session() as s:
        assert s.scalar(select(RestoreJob.id)) is None


@pytest.mark.parametrize('name,type,link',[('victim1/private',tarfile.REGTYPE,''),('demo1/../victim1/private',tarfile.REGTYPE,''),('demo1/link',tarfile.SYMTYPE,'/root/private')])
def test_restore_tree_rejects_cross_account_members_before_writing(tmp_path,monkeypatch,name,type,link):
    archive=tmp_path/'archive.tar.gz'
    with tarfile.open(archive,'w:gz') as out:
        member=tarfile.TarInfo(name);member.type=type;member.linkname=link
        if type==tarfile.REGTYPE:member.size=1
        out.addfile(member,io.BytesIO(b'x') if member.size else None)
    monkeypatch.setattr(backup,'run',lambda *a,**k:pytest.fail('unsafe archive reached extraction'))
    with pytest.raises(backup.BackupError):
        backup._restore_tree(archive,tmp_path,types.SimpleNamespace(pw_uid=5001,pw_gid=5001),prefix='demo1')
