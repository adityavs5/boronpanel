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


@pytest.mark.parametrize('name,link', [
    ('demo1/pythonapps/app/venv/bin/python3', '/root/private'),
    ('demo1/pythonapps/app/venv/bin/python3', '/usr/bin/bash'),
    ('demo1/pythonapps/app/venv/bin/python3', '../../../../../root/private'),
    ('demo1/pythonapps/app/venv/bin/python3/escape', '/usr/bin/python3'),
])
def test_interpreter_exception_rejects_unrelated_targets_and_descendants(tmp_path,monkeypatch,name,link):
    archive=tmp_path/'archive.tar'
    with tarfile.open(archive,'w') as out:
        member=tarfile.TarInfo(name);member.type=tarfile.SYMTYPE;member.linkname=link;out.addfile(member)
    monkeypatch.setattr(backup,'run',lambda *a,**k:pytest.fail('unsafe archive reached extraction'))
    with pytest.raises(backup.BackupError):
        backup._restore_tree(archive,tmp_path,types.SimpleNamespace(pw_uid=5001,pw_gid=5001),prefix='demo1')


def test_real_standard_venv_links_restore_as_account_without_changing_system_python(tmp_path):
    import os
    from pathlib import Path
    if os.geteuid()!=0:pytest.skip('Account-UID extraction requires root')
    import tempfile
    with tempfile.TemporaryDirectory(prefix='boron-qa-runtime-links-') as temporary:
        tmp_path = Path(temporary)
        tmp_path.chmod(0o755)
        home=tmp_path/'home';home.mkdir();os.chown(home,65534,65534)
        # Existing standard links are the real failing case: data_filter follows
        # them when resolving the extraction path.
        bindir=home/'pythonapps/app/venv/bin';bindir.mkdir(parents=True)
        for parent in (home/'pythonapps',home/'pythonapps/app',home/'pythonapps/app/venv',bindir):os.chown(parent,65534,65534)
        (bindir/'python3').symlink_to('/usr/bin/python3')
        (bindir/'python').symlink_to('python3')
        before=Path('/usr/bin/python3').stat()
        archive=tmp_path/'archive.tar'
        with tarfile.open(archive,'w') as out:
            for name,target in [('python3','/usr/bin/python3'),('python','python3')]:
                member=tarfile.TarInfo('demo1/pythonapps/app/venv/bin/'+name);member.type=tarfile.SYMTYPE;member.linkname=target;out.addfile(member)
            member=tarfile.TarInfo('demo1/site.txt');member.size=8;out.addfile(member,io.BytesIO(b'restored'))
        backup._restore_tree(archive,home,types.SimpleNamespace(pw_uid=65534,pw_gid=65534),prefix='demo1')
        assert (home/'site.txt').read_bytes()==b'restored'
        assert (home/'site.txt').stat().st_uid==65534
        assert os.readlink(bindir/'python3')=='/usr/bin/python3'
        assert os.readlink(bindir/'python')=='python3'
        after=Path('/usr/bin/python3').stat()
        assert (before.st_ino,before.st_mtime_ns,before.st_size)==(after.st_ino,after.st_mtime_ns,after.st_size)
