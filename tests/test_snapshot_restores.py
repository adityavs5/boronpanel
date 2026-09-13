import json
import os
from pathlib import Path
import pytest
from sqlalchemy import select
from daemon import snapshot_jobs as jobs, snapshot_storage as storage, snapshot_restores as restores
from daemon.procutil import run
from shared.config import settings
from shared.db import write_session
from shared.models import Account, SnapshotDestination, SnapshotRun, SnapshotRestore


@pytest.fixture
def environment(isolated_db,tmp_path,monkeypatch):
    if os.geteuid()!=0:pytest.skip('File restore privilege isolation requires root')
    monkeypatch.setattr(settings,'snapshot_private_dir',str(tmp_path/'private'))
    monkeypatch.setattr(settings,'home_base',str(tmp_path/'homes'))
    monkeypatch.setattr(jobs._executor,'submit',lambda *args:None)
    home=tmp_path/'homes'/'alpha';home.mkdir(parents=True);os.chown(home,65534,65534)
    (home/'site.txt').write_text('original')
    (home/'unrelated.txt').write_text('original unrelated')
    (home/'.php').mkdir()
    (home/'.php/runtime.ini').write_text('managed PHP settings')
    (home/'.php/global.ini').symlink_to('/etc/php/shared.ini')
    with write_session() as session:
        session.add(Account(username='alpha',status='active',uid=65534,gid=65534))
        session.add(Account(username='bravo',status='active',uid=65533,gid=65533))
    dest=jobs.create_destination({'name':'Restore test','path':str(tmp_path/'repo')})
    jobs.initialize_destination({'id':dest['id']})
    policy=jobs.save_policy({'name':'Files','destination_id':dest['id'],'accounts':['alpha'],'components':['files']})
    ident=jobs.queue_policy({'id':policy['id']})['run_ids'][0]
    jobs.execute_run(ident)
    assert jobs._row(SnapshotRun,ident).status=='completed'
    return tmp_path,home,ident,dest,policy


def test_real_selected_file_restore_keeps_other_files_and_safety_snapshot(environment,monkeypatch):
    root,home,ident,dest,policy=environment
    (home/'site.txt').write_text('before restore')
    (home/'unrelated.txt').write_text('keep this current content')
    (home/'new.txt').write_text('new file must remain')
    request=restores.trigger({'username':'alpha','run_id':ident,'confirmation':'alpha','paths':['site.txt']})
    assert jobs.queue_policy({'id':policy['id']})['run_ids']==[]
    from daemon import backup
    monkeypatch.setattr(backup._executor,'submit',lambda *args:pytest.fail('Conflicting legacy backup was queued'))
    with pytest.raises(Exception,match='already in progress'):
        backup.trigger_backup({'username':'alpha','kind':'full'})
    with pytest.raises(Exception,match='already in progress'):
        restores.trigger({'username':'alpha','run_id':ident,'confirmation':'alpha'})
    restores.execute(request['id'])
    result=restores.list_restores({'username':'alpha'})['restores'][0]
    assert result['status']=='completed',result['error']
    assert result['summary']['effective_uid']==65534
    assert result['summary']['nice_level']==10
    assert result['summary']['files']==1
    assert (home/'site.txt').read_text()=='original'
    assert (home/'site.txt').stat().st_uid==65534
    assert (home/'unrelated.txt').read_text()=='keep this current content'
    assert (home/'new.txt').read_text()=='new file must remain'
    assert restores.list_restores({'username':'bravo'})['restores']==[]
    repo=jobs.repository(jobs._row(SnapshotDestination,dest['id']))
    recovered=storage.restore_to(repo,result['account_id'],result['safety_snapshot_id'],str(root/'safety'))
    assert (recovered/str(home/'site.txt').lstrip('/')).read_text()=='before restore'
    assert not (root/'private/restores'/f'restore-{request["id"]}').exists()
    with pytest.raises(Exception,match='not found for this account'):
        restores.undo({'username':'bravo','restore_id':request['id'],'confirmation':'bravo'})
    reversal=restores.undo({'username':'alpha','restore_id':request['id'],'confirmation':'alpha'})
    restores.execute(reversal['id'])
    assert restores.list_restores({'username':'alpha'})['restores'][0]['status']=='completed'
    assert (home/'site.txt').read_text()=='before restore'
    assert (home/'unrelated.txt').read_text()=='keep this current content'



def test_restore_authorization_and_path_validation(environment):
    root,home,ident,_,_=environment
    with pytest.raises(Exception,match='not found for this account'):
        restores.trigger({'username':'bravo','run_id':ident,'confirmation':'bravo'})
    with pytest.raises(Exception,match='confirm'):
        restores.trigger({'username':'alpha','run_id':ident,'confirmation':'wrong'})
    with pytest.raises(Exception,match='relative'):
        restores.trigger({'username':'alpha','run_id':ident,'confirmation':'alpha','paths':['../bravo']})
    with pytest.raises(Exception,match='managed by the panel'):
        restores.trigger({'username':'alpha','run_id':ident,'confirmation':'alpha','paths':['.php/runtime.ini']})
    assert restores.list_restores({'username':'alpha'})['restores']==[]


def test_missing_snapshot_path_never_changes_live_files(environment):
    root,home,ident,_,_=environment
    request=restores.trigger({'username':'alpha','run_id':ident,'confirmation':'alpha','paths':['missing.txt']})
    restores.execute(request['id'])
    result=restores.list_restores({'username':'alpha'})['restores'][0]
    assert result['status']=='failed'
    assert 'not found' in result['error']
    assert (home/'site.txt').read_text()=='original'
    assert result['safety_snapshot_id'] is None


def worker(source,home,paths):
    return run(['/usr/bin/python3','-I',str(Path(restores.__file__).with_name('snapshot_file_worker.py'))],
        input_text=json.dumps(dict(source=str(source),home=str(home),uid=65534,gid=65534,paths=paths)),timeout=30)


def test_worker_never_follows_target_directory_links(tmp_path):
    if os.geteuid()!=0:pytest.skip('requires root')
    source=tmp_path/'source';(source/'folder').mkdir(parents=True);(source/'folder/file').write_text('replacement')
    home=tmp_path/'home';home.mkdir();os.chown(home,65534,65534)
    outside=tmp_path/'outside';outside.mkdir();(outside/'file').write_text('protected')
    (home/'folder').symlink_to(outside,target_is_directory=True)
    result=worker(source,home,['folder'])
    assert not result.ok
    assert (outside/'file').read_text()=='protected'


def test_worker_rejects_escaping_source_links_before_any_write(tmp_path):
    if os.geteuid()!=0:pytest.skip('requires root')
    source=tmp_path/'source';source.mkdir();(source/'site').write_text('replacement');(source/'escape').symlink_to('/etc/shadow')
    home=tmp_path/'home';home.mkdir();os.chown(home,65534,65534);(home/'site').write_text('original')
    result=worker(source,home,['.'])
    assert not result.ok
    assert 'escapes' in result.stderr
    assert (home/'site').read_text()=='original'


def test_worker_preserves_internal_links_and_strips_privileged_modes(tmp_path):
    if os.geteuid()!=0:pytest.skip('requires root')
    source=tmp_path/'source';source.mkdir();(source/'file').write_text('restored');(source/'file').chmod(0o6755)
    (source/'link').symlink_to('file')
    home=tmp_path/'home';home.mkdir();os.chown(home,65534,65534)
    result=worker(source,home,['.'])
    assert result.ok,result.stderr
    assert (home/'file').read_text()=='restored'
    assert (home/'file').stat().st_mode & 0o7777 == 0o755
    assert (home/'link').is_symlink()
    assert os.readlink(home/'link')=='file'


def test_real_all_captured_files_restore_keeps_new_files_and_home_permissions(environment):
    root,home,ident,_,_=environment
    home.chmod(0o750)
    (home/'site.txt').write_text('changed website')
    (home/'unrelated.txt').write_text('changed second file')
    (home/'new.txt').write_text('created after backup')
    (home/'.php/runtime.ini').write_text('current protected PHP settings')
    request=restores.trigger({'username':'alpha','run_id':ident,'confirmation':'alpha','paths':[]})
    restores.execute(request['id'])
    result=restores.list_restores({'username':'alpha'})['restores'][0]
    assert result['status']=='completed',result['error']
    assert (home/'site.txt').read_text()=='original'
    assert (home/'unrelated.txt').read_text()=='original unrelated'
    assert (home/'new.txt').read_text()=='created after backup'
    assert home.stat().st_mode & 0o777 == 0o750
    assert (home/'.php').stat().st_uid==0
    assert (home/'.php/runtime.ini').read_text()=='current protected PHP settings'
    assert next(r for r in jobs.browse({'username':'alpha','run_id':ident,'directory':str(home)})['entries'] if r['name']=='.php')['restore_path'] is None
    assert result['summary']['effective_uid']==65534


def test_safety_retention_keeps_failed_and_queued_recovery_copies(environment):
    root, home, ident, dest, policy = environment
    rows = []
    for index in range(4):
        (home / 'site.txt').write_text(f'previous version {index}')
        request = restores.trigger({'username': 'alpha', 'run_id': ident,
                                    'confirmation': 'alpha', 'paths': ['site.txt']})
        restores.execute(request['id'])
        row = jobs._row(SnapshotRestore, request['id'])
        assert row.status == 'completed', row.error
        rows.append(row)
    # Failed restores are retained even when newer successful points exist.
    restores._update(rows[0].id, status='failed')
    queued = restores.undo({'username': 'alpha', 'restore_id': rows[1].id, 'confirmation': 'alpha'})
    repo = jobs.repository(jobs._row(SnapshotDestination, dest['id']))
    assert restores.apply_safety_retention(repo, rows[0].account_id, dest['id'], policy['id'], 1) == 1
    points = {item['id'] for item in storage.snapshots(repo, rows[0].account_id)}
    assert rows[0].safety_snapshot_id in points
    assert rows[1].safety_snapshot_id in points
    assert rows[2].safety_snapshot_id not in points
    assert rows[3].safety_snapshot_id in points
    expired = jobs._row(SnapshotRestore, rows[2].id)
    assert expired.safety_snapshot_id is None
    assert expired.summary['safety_snapshot_expired'] is True
    with pytest.raises(Exception, match='recovery point not found'):
        restores.undo({'username': 'alpha', 'restore_id': rows[2].id, 'confirmation': 'alpha'})
    restores.execute(queued['id'])
    assert jobs._row(SnapshotRestore, queued['id']).status == 'completed'
    assert (home / 'site.txt').read_text() == 'previous version 1'


def test_safety_retention_failure_keeps_metadata_and_retries_missing_snapshot(environment, monkeypatch):
    root, home, ident, dest, policy = environment
    rows = []
    for index in range(2):
        (home / 'site.txt').write_text(f'recovery {index}')
        request = restores.trigger({'username': 'alpha', 'run_id': ident,
                                    'confirmation': 'alpha', 'paths': ['site.txt']})
        restores.execute(request['id'])
        rows.append(jobs._row(SnapshotRestore, request['id']))
    repo = jobs.repository(jobs._row(SnapshotDestination, dest['id']))
    original = storage.forget
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('Interrupted after repository deletion')
    monkeypatch.setattr(storage, 'forget', interrupted)
    with pytest.raises(RuntimeError, match='Interrupted'):
        restores.apply_safety_retention(repo, rows[0].account_id, dest['id'], policy['id'], 1)
    assert jobs._row(SnapshotRestore, rows[0].id).safety_snapshot_id
    monkeypatch.setattr(storage, 'forget', original)
    assert restores.apply_safety_retention(repo, rows[0].account_id, dest['id'], policy['id'], 1) == 1
    assert jobs._row(SnapshotRestore, rows[0].id).safety_snapshot_id is None
    assert jobs._row(SnapshotRestore, rows[1].id).safety_snapshot_id
