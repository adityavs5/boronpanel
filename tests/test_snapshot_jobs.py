from pathlib import Path
import pytest
from sqlalchemy import select
from daemon import snapshot_jobs as jobs, snapshot_storage as storage
from shared.config import settings
from shared.db import write_session
from shared.models import Account, SnapshotDestination, SnapshotPolicy, SnapshotRun


@pytest.fixture
def environment(isolated_db,tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'snapshot_private_dir',str(tmp_path/'private'))
    monkeypatch.setattr(settings,'home_base',str(tmp_path/'home'))
    queued=[]
    monkeypatch.setattr(jobs._executor,'submit',lambda fn,ident:queued.append((fn,ident)))
    for name in ['alpha','bravo']:
        home=tmp_path/'home'/name;home.mkdir(parents=True)
        (home/'site.txt').write_text(name+' original content')
        (home/'exclude.txt').write_text('excluded content')
        with write_session() as session:
            session.add(Account(username=name,status='active',uid=2000,gid=2000))
    return tmp_path,queued


def make_destination(tmp_path):
    dest=jobs.create_destination({'name':'Local encrypted','path':str(tmp_path/'repository')})
    assert dest['status']=='draft'
    jobs.initialize_destination({'id':dest['id']})
    return dest


def make_policy(dest,**kwargs):
    return jobs.save_policy(dict(name='Daily websites',destination_id=dest['id'],components=['files'],
        accounts=['alpha'],exclude_patterns=['exclude.txt'],frequency='daily',retention_count=1,**kwargs))


def test_real_persistent_job_incremental_retention_and_restore(environment):
    root,queued=environment
    dest=make_destination(root);policy=make_policy(dest)
    response=jobs.queue_policy({'id':policy['id']})
    ident=response['run_ids'][0]
    assert len(queued)==1
    assert jobs.queue_policy({'id':policy['id']})['skipped_busy_accounts']==['alpha']
    jobs.execute_run(ident)
    row=jobs.runs({'username':'alpha'})['runs'][0]
    assert row['status']=='completed',row['error']
    assert jobs.runs({'username':'bravo'})['runs']==[]
    assert 'accounts' not in row['options']
    assert 'notification_channels' not in row['options']
    repo=jobs.repository(jobs._row(SnapshotDestination,dest['id']))
    first=storage.restore_to(repo,row['account_id'],row['snapshot_id'],str(root/'restore'))
    assert (first/str(root/'home/alpha/site.txt').lstrip('/')).read_text()=='alpha original content'
    assert not (first/str(root/'home/alpha/exclude.txt').lstrip('/')).exists()
    assert jobs.browse({'username':'alpha','run_id':ident,'directory':str(root/'home/alpha')})['entries']
    assert any(r['name']=='Account files' for r in jobs.browse({'username':'alpha','run_id':ident})['entries'])
    with jobs.lock(f'repository-{dest["id"]}'):
        with pytest.raises(Exception,match='busy'):
            jobs.browse({'username':'alpha','run_id':ident})
    with pytest.raises(Exception,match='not found for this account'):
        jobs.browse({'username':'bravo','run_id':ident})
    assert jobs.queue_policy({'id':policy['id'],'trigger':'scheduled'})['run_ids']==[]
    second=jobs.queue_policy({'id':policy['id']})['run_ids'][0]
    jobs.execute_run(second)
    latest=jobs.runs({'username':'alpha'})['runs'][0]
    assert latest['status']=='completed',latest['error']
    assert latest['summary']['files_unmodified']>=1
    assert len(storage.snapshots(repo,row['account_id']))==1
    assert storage.snapshots(repo,row['account_id'])[0]['id']==latest['snapshot_id']
    recovered=storage.restore_to(repo,row['account_id'],latest['snapshot_id'],str(root/'latest'))
    assert (recovered/str(root/'home/alpha/site.txt').lstrip('/')).read_text()=='alpha original content'


def test_credentials_never_in_list_or_database(environment):
    root,_=environment
    dest=jobs.create_destination({'name':'Local','path':str(root/'repository')})
    recovery=jobs.recovery_key({'id':dest['id']})
    assert len(recovery['password'])>=60
    assert recovery['password'] not in str(jobs.destinations({}))
    row=jobs._row(SnapshotDestination,dest['id']);spec=jobs.repository(row)
    assert Path(spec.password_file).stat().st_mode & 0o777 == 0o600
    assert recovery['password'].encode() not in Path(settings.db_path).read_bytes()
    with pytest.raises(Exception,match='already exists'):
        jobs.create_destination({'name':'Local','path':str(root/'elsewhere')})
    assert Path(spec.password_file).read_text().strip()==recovery['password']


@pytest.mark.parametrize('options,match',[
    ({'include_paths':['../../etc']},'relative'),
    ({'include_paths':['/etc']},'relative'),
    ({'retention_count':0},'between'),
    ({'components':['unknown']},'components'),
    ({'notification_channels':['sms']},'email or webhook'),
    ({'mode':'fake'},'full or incremental'),
    ({'accounts':['missing']},'Account not found'),
])
def test_rejects_invalid_policy_options(environment,options,match):
    with pytest.raises(Exception,match=match):jobs.validate_options(options)


def test_source_symlink_escape_fails_and_marks_job_failed(environment,monkeypatch):
    root,_=environment
    dest=make_destination(root)
    (root/'home/alpha/escape').symlink_to('/etc')
    policy=make_policy(dest,include_paths=['escape/passwd'],notification_channels=['email','webhook'])
    from daemon import notifications,webhooks
    calls=[]
    monkeypatch.setattr(notifications,'maybe_send',lambda *a,**kw:calls.append('email') or True)
    monkeypatch.setattr(webhooks,'maybe_trigger',lambda *a,**kw:calls.append('webhook') or [1])
    ident=jobs.queue_policy({'id':policy['id']})['run_ids'][0]
    jobs.execute_run(ident)
    row=jobs.runs({'username':'alpha'})['runs'][0]
    assert row['status']=='failed'
    assert 'outside' in row['error']
    assert calls==['email','webhook']
    assert row['notification_results']=={'email':'dispatched','webhook':'dispatched'}
    jobs.execute_run(ident)
    assert calls==['email','webhook']


def test_frozen_options_filters_and_recovery(environment):
    root,queued=environment
    dest=make_destination(root)
    policy=jobs.save_policy(dict(name='All except bravo',destination_id=dest['id'],accounts=[],excluded_accounts=['bravo'],components=['files']))
    ident=jobs.queue_policy({'id':policy['id']})['run_ids'][0]
    jobs.save_policy(dict(id=policy['id'],name=policy['name'],destination_id=dest['id'],components=['mail']))
    assert jobs._row(SnapshotRun,ident).options['components']==['files']
    jobs._update(ident,status='running')
    with jobs.lock(f'account-{jobs._row(SnapshotRun,ident).account_id}'):
        jobs.recover_runs()
        assert jobs._row(SnapshotRun,ident).status=='running'
    jobs.recover_runs()
    assert jobs._row(SnapshotRun,ident).status=='failed'
    assert 'interrupted' in jobs._row(SnapshotRun,ident).error


def test_snapshot_api_authorization_and_customer_scope(environment,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers import backups,account_backups
    from api.security import Identity,get_identity
    app=FastAPI();app.include_router(backups.api_router);app.include_router(account_backups.api_router)
    identity=Identity(panel_user_id=1,username='alpha',role='customer',account_id=1,auth_method='session')
    app.dependency_overrides[get_identity]=lambda:identity
    calls=[]
    def rpc(op,identity,**params):calls.append((op,params));return {'runs':[]}
    monkeypatch.setattr(backups,'call_daemon',rpc);monkeypatch.setattr(account_backups,'call_daemon',rpc)
    with TestClient(app) as client:
        for path in ['destinations','policies','runs']:
            assert client.get('/api/v1/backups/snapshots/'+path).status_code==403
        assert client.post('/api/v1/backups/snapshots/destinations/1/recovery-key').status_code==403
        assert not calls
        assert client.get('/api/v1/accounts/alpha/backups/snapshots/runs').status_code==200
        assert calls[-1]==('snapshot.run.list',{'username':'alpha'})
        assert client.get('/api/v1/accounts/bravo/backups/snapshots/runs').status_code==403
        response=client.post('/api/v1/accounts/alpha/backups/snapshots/runs/1/restore',json={'confirmation':'alpha','paths':['site.txt'],'_safety':999})
        assert response.status_code==200
        assert calls[-1][0]=='snapshot.restore.trigger'
        assert calls[-1][1]['username']=='alpha'
        assert '_safety' not in calls[-1][1]
        response=client.post('/api/v1/accounts/alpha/backups/snapshots/runs/1/restore',json={'confirmation':'alpha','kind':'databases','databases':['alpha_wp']})
        assert response.status_code==200
        assert calls[-1][1]['kind']=='databases'
        assert calls[-1][1]['databases']==['alpha_wp']
        assert client.post('/api/v1/accounts/bravo/backups/snapshots/runs/1/restore',json={'confirmation':'bravo'}).status_code==403
        assert client.post('/api/v1/accounts/bravo/backups/snapshots/restores/1/undo',json={'confirmation':'bravo'}).status_code==403
        assert client.get('/api/v1/accounts/bravo/backups/snapshots/restores').status_code==403
        identity.role='admin'
        response=client.post('/api/v1/backups/snapshots/destinations/1/recovery-key')
        assert response.status_code==200
        assert response.headers['cache-control']=='no-store'
