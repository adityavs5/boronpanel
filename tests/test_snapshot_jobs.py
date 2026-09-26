from pathlib import Path
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit
from types import SimpleNamespace
import datetime as dt
import pytest
from sqlalchemy import select
from daemon import snapshot_jobs as jobs, snapshot_storage as storage
from shared.config import settings
from shared.db import write_session
from shared.models import Account, SnapshotDestination, SnapshotDownload, SnapshotPolicy, SnapshotRun, utcnow


@pytest.fixture
def environment(isolated_db,tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'snapshot_private_dir',str(tmp_path/'private'))
    monkeypatch.setattr(settings,'snapshot_download_dir',str(tmp_path/'downloads'))
    monkeypatch.setattr(settings,'home_base',str(tmp_path/'home'))
    queued=[]
    monkeypatch.setattr(jobs._executor,'submit',lambda fn,ident:queued.append((fn,ident)))
    monkeypatch.setattr(jobs.pwd,'getpwnam',lambda _name:SimpleNamespace(pw_uid=__import__('os').geteuid(),pw_gid=__import__('os').getegid()))
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
    options=dict(name='Daily websites',destination_id=dest['id'],components=['files'],
        accounts=['alpha'],exclude_patterns=['exclude.txt'],frequency='daily',retention_count=1)
    options.update(kwargs)
    return jobs.save_policy(options)


def test_calendar_schedule_respects_local_time_and_period(environment):
    root,_=environment
    destination=make_destination(root)
    policy=make_policy(destination,timezone='Asia/Kolkata',schedule_hour=2,schedule_minute=30)
    with write_session() as session:
        row=session.get(SnapshotPolicy,policy['id'])
        row.created_at=dt.datetime(2026,9,24,0,0,tzinfo=dt.timezone.utc)
        assert jobs.scheduled_boundary(row,dt.datetime(2026,9,25,0,0,tzinfo=dt.timezone.utc))==dt.datetime(2026,9,24,21,0,tzinfo=dt.timezone.utc)
        assert jobs.scheduled_due(row,dt.datetime(2026,9,25,0,0,tzinfo=dt.timezone.utc)) is True
        row.last_queued_at=dt.datetime(2026,9,24,21,1,tzinfo=dt.timezone.utc)
        assert jobs.scheduled_due(row,dt.datetime(2026,9,25,0,0,tzinfo=dt.timezone.utc)) is False


def test_weekly_and_monthly_schedule_boundaries(environment):
    root,_=environment
    destination=make_destination(root)
    weekly=make_policy(destination,name='Weekly',frequency='weekly',schedule_weekday=0,schedule_hour=4)
    monthly=make_policy(destination,name='Monthly',frequency='monthly',schedule_monthday=31,schedule_hour=1)
    with write_session() as session:
        weekly_row=session.get(SnapshotPolicy,weekly['id']);monthly_row=session.get(SnapshotPolicy,monthly['id'])
        now=dt.datetime(2026,9,25,12,0,tzinfo=dt.timezone.utc)  # Friday
        assert jobs.scheduled_boundary(weekly_row,now)==dt.datetime(2026,9,21,4,0,tzinfo=dt.timezone.utc)
        # September has 30 days, so a day-31 job uses the final day.
        assert jobs.scheduled_boundary(monthly_row,dt.datetime(2026,9,30,2,0,tzinfo=dt.timezone.utc))==dt.datetime(2026,9,30,1,0,tzinfo=dt.timezone.utc)


def test_policy_api_preserves_consistency_and_calendar_fields():
    from api.routers.backups import SnapshotPolicyBody
    body=SnapshotPolicyBody(name='Scheduled',destination_id=1,quiesce_apps=True,
        timezone='Asia/Kolkata',schedule_hour=3,schedule_minute=15,
        schedule_weekday=4,schedule_monthday=28)
    values=body.model_dump()
    assert values['quiesce_apps'] is True
    assert {key:values[key] for key in ('timezone','schedule_hour','schedule_minute','schedule_weekday','schedule_monthday')}=={
        'timezone':'Asia/Kolkata','schedule_hour':3,'schedule_minute':15,'schedule_weekday':4,'schedule_monthday':28}


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


def test_s3_credentials_are_encrypted_and_write_only(environment):
    root, _ = environment
    access = 'test-access-key'
    secret = 'test-secret-key-that-must-not-leak'
    dest = jobs.create_destination({'name':'S3 offsite','kind':'s3','s3_provider':'custom',
        's3_endpoint':'https://objects.example.test','s3_bucket':'hosting-backups','s3_prefix':'daily',
        's3_region':'test-region-1','s3_access_key':access,'s3_secret_key':secret})
    listed = jobs.destinations({})['destinations'][0]
    assert listed['kind'] == 's3'
    assert listed['connection']['bucket'] == 'hosting-backups'
    assert access not in str(listed) and secret not in str(listed)
    row = jobs._row(SnapshotDestination, dest['id'])
    spec = jobs.repository(row)
    credentials = Path(settings.snapshot_private_dir)/'repositories'/row.namespace/'s3_credentials.enc'
    assert credentials.stat().st_mode & 0o777 == 0o600
    assert access.encode() not in credentials.read_bytes()
    assert secret.encode() not in credentials.read_bytes()
    assert access.encode() not in Path(settings.db_path).read_bytes()
    assert secret.encode() not in Path(settings.db_path).read_bytes()
    assert spec.s3_credentials == {'access_key':access,'secret_key':secret,'session_token':''}


def test_ssh_password_is_encrypted_and_kept_off_command_line(environment):
    root,_=environment
    secret='test-password-never-in-argv'
    destination=jobs.create_destination({'name':'Password SFTP','kind':'sftp','path':'/srv/backups',
        'ssh_host':'backup.example.test','ssh_user':'boron','ssh_port':2222,'ssh_auth':'password',
        'ssh_password':secret,'ssh_host_key':'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestOnlyHostKey'})
    row=jobs._row(SnapshotDestination,destination['id']);spec=jobs.repository(row)
    encrypted=Path(settings.snapshot_private_dir)/'repositories'/row.namespace/'transport_credentials.enc'
    assert encrypted.stat().st_mode & 0o777 == 0o600
    assert secret.encode() not in encrypted.read_bytes() and secret.encode() not in Path(settings.db_path).read_bytes()
    assert secret not in ' '.join(spec.arguments())
    assert spec.ssh_password == secret and spec.ssh_auth == 'password'


def test_drive_rejects_incremental_but_accepts_archive_policy(environment):
    root,_=environment
    destination=jobs.create_destination({'name':'Drive archive','kind':'drive','drive_folder':'Boron backups',
        'drive_client_id':'client.apps.test','drive_client_secret':'private-client-secret',
        'drive_token':'{"access_token":"access","refresh_token":"refresh","expiry":"2099-01-01T00:00:00Z"}'})
    with write_session() as session:
        row=session.get(SnapshotDestination,destination['id']);row.status='ready'
    with pytest.raises(Exception,match='not supported'):
        jobs.save_policy({'name':'Bad Drive incremental','destination_id':destination['id'],
            'accounts':['alpha'],'components':['files'],'mode':'incremental'})
    policy=jobs.save_policy({'name':'Drive archives','destination_id':destination['id'],
        'accounts':['alpha'],'components':['files'],'mode':'compressed'})
    assert policy['options']['mode']=='compressed'


def test_drive_browser_oauth_folder_selection_and_revoke(environment,monkeypatch):
    root,_=environment
    client_secret='oauth-client-secret-never-in-database'
    destination=jobs.create_destination({'name':'Drive OAuth','kind':'drive','drive_folder':'Boron backups',
        'drive_client_id':'client.apps.test','drive_client_secret':client_secret})
    assert destination['connection']['oauth_connected'] is False
    assert client_secret.encode() not in Path(settings.db_path).read_bytes()

    started=jobs.drive_oauth_start({'id':destination['id'],'redirect_uri':
        f'https://panel.example.test/api/v1/backups/snapshots/destinations/{destination["id"]}/drive/oauth/callback'})
    query=parse_qs(urlsplit(started['authorization_url']).query)
    assert query['code_challenge_method']==['S256'] and query['access_type']==['offline']

    class Response:
        def raise_for_status(self):pass
        def json(self):return {'access_token':'access-secret','refresh_token':'refresh-secret','expires_in':3600}
    monkeypatch.setattr(jobs.httpx,'post',lambda *a,**k:Response())
    connected=jobs.drive_oauth_callback({'id':destination['id'],'state':query['state'][0],'code':'one-time-code'})
    assert connected['connection']['oauth_connected'] is True
    config=Path(settings.snapshot_private_dir)/'repositories'/connected['namespace']/'rclone.conf'
    assert config.stat().st_mode & 0o077==0 and 'refresh-secret' in config.read_text()

    monkeypatch.setattr(jobs,'run',lambda *a,**k:SimpleNamespace(stdout='[{"Name":"Backups","Path":"Backups","ID":"folder-1","IsDir":true}]',raise_if_failed=lambda *_:None))
    assert jobs.drive_folders({'id':destination['id']})['folders'][0]['path']=='Backups'
    assert jobs.set_drive_folder({'id':destination['id'],'path':'Backups'})['path']=='Backups'
    revoked=jobs.revoke_drive({'id':destination['id']})
    assert revoked['connection']['oauth_connected'] is False and not config.exists()


def test_download_is_queued_persistent_private_and_expires(environment):
    root,queued=environment
    destination=make_destination(root);policy=make_policy(destination)
    run_id=jobs.queue_policy({'id':policy['id']})['run_ids'][0]
    jobs.execute_run(run_id)

    item=jobs.queue_download({'username':'alpha','run_id':run_id,'customer_scope':True})
    assert item['status']=='pending'
    assert queued[-1][0] is jobs.execute_download
    assert jobs.queue_download({'username':'alpha','run_id':run_id})['id']==item['id']

    jobs.execute_download(item['id'])
    ready=jobs.list_downloads({'username':'alpha','customer_scope':True})['downloads'][0]
    assert ready['status']=='ready' and ready['sha256'] and ready['size_bytes']>0
    prepared=jobs.download_file({'username':'alpha','id':item['id'],'customer_scope':True})
    path=Path(prepared['path'])
    assert path.is_file() and path.is_relative_to(Path(settings.snapshot_download_dir))
    assert '/tmp/boron-snapshot-download-' not in str(path)

    with write_session() as session:
        row=session.get(SnapshotDownload,item['id']);row.expires_at=utcnow()-__import__('datetime').timedelta(seconds=1)
    assert jobs.cleanup_downloads()=={'removed':1}
    assert not path.exists()
    with pytest.raises(Exception,match='not ready or has expired'):
        jobs.download_file({'username':'alpha','id':item['id']})


def test_hidden_destination_is_absent_from_every_customer_recovery_surface(environment):
    root,_=environment
    destination=make_destination(root);policy=make_policy(destination)
    run_id=jobs.queue_policy({'id':policy['id']})['run_ids'][0]
    jobs.execute_run(run_id)
    jobs.set_destination({'id':destination['id'],'customer_visible':False})
    assert jobs.runs({'username':'alpha','customer_scope':True})['runs']==[]
    with pytest.raises(Exception,match='not available in the customer panel'):
        jobs.browse({'username':'alpha','run_id':run_id,'customer_scope':True})
    with pytest.raises(Exception,match='not available in the customer panel'):
        jobs.queue_download({'username':'alpha','run_id':run_id,'customer_scope':True})


@pytest.mark.parametrize('mode,suffix', [('compressed','.boron.tar.gz'),('archive','.boron.tar')])
def test_portable_modes_embed_verified_account_archive(environment,mode,suffix):
    root,_=environment
    destination=make_destination(root)
    policy=make_policy(destination,mode=mode)
    ident=jobs.queue_policy({'id':policy['id']})['run_ids'][0]
    jobs.execute_run(ident)
    row=jobs._row(SnapshotRun,ident)
    assert row.status=='completed',row.error
    portable=row.summary['portable_archive'];assert portable['name'].endswith(suffix)
    repository=jobs.repository(jobs._row(SnapshotDestination,destination['id']))
    restored=storage.restore_to(repository,row.account_id,row.snapshot_id,str(root/f'restore-{mode}'),
        selected_paths=[portable['path']])
    archive=restored/portable['path'].lstrip('/')
    import hashlib,tarfile
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==portable['sha256']
    with tarfile.open(archive) as handle:
        names=handle.getnames()
        assert 'account/manifest.json' in names
        assert 'account/home/site.txt' in names
        assert 'account/home/exclude.txt' not in names


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


def test_multi_destination_batch_reports_partial_once(environment,monkeypatch):
    root,_=environment
    first=jobs.create_destination({'name':'First','path':str(root/'repository-one')});jobs.initialize_destination({'id':first['id']})
    second=jobs.create_destination({'name':'Second','path':str(root/'repository-two')});jobs.initialize_destination({'id':second['id']})
    policy=make_policy(first,destination_ids=[first['id'],second['id']],notification_channels=['email'])
    run_ids=jobs.queue_policy({'id':policy['id']})['run_ids']
    calls=[]
    from daemon import backup_notifications
    monkeypatch.setattr(backup_notifications,'dispatch',lambda event,*args,**kwargs:calls.append((event,kwargs['job_id'])) or {'email':'queued'})
    with write_session() as session:
        session.get(SnapshotRun,run_ids[0]).status='completed'
        session.get(SnapshotRun,run_ids[1]).status='running'
    account=jobs._row(Account,jobs._row(SnapshotRun,run_ids[0]).account_id)
    jobs._notify(jobs._row(SnapshotRun,run_ids[0]),account)
    assert calls==[]
    with write_session() as session:session.get(SnapshotRun,run_ids[1]).status='failed'
    jobs._notify(jobs._row(SnapshotRun,run_ids[1]),account)
    assert calls==[('backup.partial',min(run_ids))]
    assert all(jobs._row(SnapshotRun,ident).notification_results=={'email':'queued'} for ident in run_ids)


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
    assert row['notification_results']=={'email':'provider accepted','webhook':'queued'}
    jobs.execute_run(ident)
    assert calls==['email','webhook']


def test_execute_run_rechecks_account_status_inside_lock(environment, monkeypatch):
    root, _ = environment
    dest = make_destination(root)
    policy = make_policy(dest)
    ident = jobs.queue_policy({'id': policy['id']})['run_ids'][0]
    account_id = jobs._row(SnapshotRun, ident).account_id
    original_lock = jobs.lock
    flipped = False

    @contextmanager
    def lock_and_suspend(name, *args, **kwargs):
        nonlocal flipped
        with original_lock(name, *args, **kwargs):
            if name == f'account-{account_id}' and not flipped:
                with write_session() as session:
                    session.get(Account, account_id).status = 'terminated'
                flipped = True
            yield

    monkeypatch.setattr(jobs, 'lock', lock_and_suspend)
    monkeypatch.setattr(jobs, 'sources', lambda *_a, **_k: pytest.fail('stale snapshot run must not collect sources'))

    jobs.execute_run(ident)

    row = jobs._row(SnapshotRun, ident)
    assert row.status == 'failed'
    assert 'no longer active' in row.error


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
        assert calls[-1]==('snapshot.run.list',{'username':'alpha','customer_scope':True})
        assert client.get('/api/v1/accounts/bravo/backups/snapshots/runs').status_code==403
        assert client.get('/api/v1/accounts/alpha/backups/snapshots/runs/1/configuration').status_code==200
        assert calls[-1]==('snapshot.restore.configuration',{'username':'alpha','run_id':1,'customer_scope':True})
        assert client.get('/api/v1/accounts/bravo/backups/snapshots/runs/1/configuration').status_code==403
        response=client.post('/api/v1/accounts/alpha/backups/snapshots/runs/1/restore',json={'confirmation':'alpha','kind':'config','config_sections':['cron']})
        assert response.status_code==200
        assert calls[-1][1]['config_sections']==['cron']
        response=client.post('/api/v1/accounts/alpha/backups/snapshots/runs/1/restore',json={'confirmation':'alpha','kind':'config','config_sections':['dns'],'dns_zones':['alpha.test']})
        assert response.status_code==200
        assert calls[-1][1]['dns_zones']==['alpha.test']
        assert calls[-1][1]['config_sections']==['dns']

        response=client.post('/api/v1/accounts/alpha/backups/snapshots/runs/1/restore',json={'confirmation':'alpha','paths':['site.txt'],'_safety':999})
        assert response.status_code==200
        assert calls[-1][0]=='snapshot.restore.trigger'
        assert calls[-1][1]['username']=='alpha'
        assert '_safety' not in calls[-1][1]
        response=client.post('/api/v1/accounts/alpha/backups/snapshots/runs/1/restore',json={'confirmation':'alpha','kind':'databases','databases':['alpha_wp']})
        assert response.status_code==200
        assert calls[-1][1]['kind']=='databases'
        assert calls[-1][1]['databases']==['alpha_wp']
        response=client.post('/api/v1/accounts/alpha/backups/snapshots/runs/1/restore',json={
            'confirmation':'alpha','kind':'mail','mailboxes':['inbox@example.test'],'mail_pause_acknowledged':True})
        assert response.status_code==200
        assert calls[-1][1]['mailboxes']==['inbox@example.test']
        assert calls[-1][1]['mail_pause_acknowledged'] is True
        response=client.post('/api/v1/accounts/alpha/backups/snapshots/runs/1/restore',json={
            'confirmation':'alpha','kind':'mail_routing','mail_domains':['alpha.example.test'],'mail_pause_acknowledged':True})
        assert response.status_code==200
        assert calls[-1][1]['mail_domains']==['alpha.example.test']
        assert calls[-1][1]['kind']=='mail_routing'
        response=client.post('/api/v1/accounts/alpha/backups/snapshots/restores/1/undo',json={
            'confirmation':'alpha','mail_pause_acknowledged':True})
        assert response.status_code==200
        assert calls[-1]==('snapshot.restore.undo',{'username':'alpha','restore_id':1,'confirmation':'alpha','mail_pause_acknowledged':True,'customer_scope':True})
        assert client.post('/api/v1/accounts/bravo/backups/snapshots/runs/1/restore',json={
            'confirmation':'bravo','kind':'mail','mailboxes':['inbox@example.test'],'mail_pause_acknowledged':True}).status_code==403
        assert client.get('/api/v1/accounts/alpha/backups/snapshots/runs/1/databases').status_code==200
        assert calls[-1]==('snapshot.restore.databases',{'username':'alpha','run_id':1,'customer_scope':True})
        assert client.get('/api/v1/accounts/alpha/backups/snapshots/runs/1/mailboxes').status_code==200
        assert calls[-1]==('snapshot.restore.mailboxes',{'username':'alpha','run_id':1,'customer_scope':True})
        assert client.get('/api/v1/accounts/bravo/backups/snapshots/runs/1/mailboxes').status_code==403
        assert client.get('/api/v1/accounts/alpha/backups/snapshots/runs/1/mail-routing').status_code==200
        assert calls[-1]==('snapshot.restore.mail_routing',{'username':'alpha','run_id':1,'customer_scope':True})
        before_calls=len(calls)
        assert client.get('/api/v1/accounts/bravo/backups/snapshots/runs/1/mail-routing').status_code==403
        assert len(calls)==before_calls
        assert client.get('/api/v1/accounts/bravo/backups/snapshots/runs/1/databases').status_code==403
        assert client.post('/api/v1/accounts/bravo/backups/snapshots/runs/1/restore',json={'confirmation':'bravo'}).status_code==403
        assert client.post('/api/v1/accounts/bravo/backups/snapshots/restores/1/undo',json={'confirmation':'bravo'}).status_code==403
        assert client.get('/api/v1/accounts/bravo/backups/snapshots/restores').status_code==403
        identity.role='admin'
        response=client.post('/api/v1/backups/snapshots/destinations/1/recovery-key')
        assert response.status_code==200
        assert response.headers['cache-control']=='no-store'


def test_configuration_snapshot_preserves_complete_crontab(environment, monkeypatch):
    import json
    from daemon import cron
    root, _ = environment
    raw = ['SHELL=/bin/bash', 'MAILTO=""', '# manually managed task',
           '0 2 * * * /bin/true', '# boron:id=aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa label=hourly',
           '@hourly /usr/bin/php /home/alpha/hourly.php']
    reads = []
    def read(username):
        reads.append(username)
        return list(raw)
    monkeypatch.setattr(cron, '_read_raw', read)
    from shared.models import PhpIniOverride, PhpIniDirective, PhpExtensionSet
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == 'alpha'))
        account.php_version = '8.2'
        session.add(PhpIniOverride(account_id=account.id, memory_limit='512M'))
        session.add(PhpIniDirective(account_id=account.id, name='max_input_vars', value='6000'))
        session.add(PhpExtensionSet(account_id=account.id, enabled=[]))
    dest = make_destination(root)
    policy = jobs.save_policy(dict(name='Account configuration', destination_id=dest['id'],
                                    accounts=['alpha'], components=['config'], frequency='manual'))
    ident = jobs.queue_policy({'id': policy['id']})['run_ids'][0]
    jobs.execute_run(ident)
    row = jobs._row(SnapshotRun, ident)
    assert row.status == 'completed', row.error
    assert reads == ['alpha']
    repository = jobs.repository(jobs._row(SnapshotDestination, dest['id']))
    restored = storage.restore_to(repository, row.account_id, row.snapshot_id, str(root/'configuration-proof'))
    manifests = list(restored.rglob('manifest.json'))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    assert manifest['cron_configuration'] == {'format': 1, 'username': 'alpha', 'lines': raw}
    assert manifest['php_configuration']['default_version'] == '8.2'
    assert manifest['php_configuration']['ini_override']['memory_limit'] == '512M'
    assert manifest['php_configuration']['extra_directives'] == {'max_input_vars': '6000'}
    assert manifest['php_configuration']['enabled_extensions'] == []
    assert manifest['cron_jobs'][0]['schedule'] == '@hourly'
    assert manifest['cron_jobs'][0]['command'] == '/usr/bin/php /home/alpha/hourly.php'


def test_policy_queues_each_account_to_multiple_destinations(environment):
    root, queued = environment
    first = make_destination(root)
    second = jobs.create_destination({'name':'Second local','path':str(root/'repository-two')})
    jobs.initialize_destination({'id':second['id']})
    policy = jobs.save_policy({'name':'Mirrored','destination_id':first['id'],
        'destination_ids':[first['id'],second['id']],'components':['files'],'accounts':['alpha']})
    result = jobs.queue_policy({'id':policy['id']})
    assert len(result['run_ids']) == 2
    assert {jobs._row(SnapshotRun, ident).destination_id for ident in result['run_ids']} == {first['id'],second['id']}
    assert len(queued) == 2


def test_destination_visibility_disable_and_safe_delete(environment):
    root, _ = environment
    destination = jobs.create_destination({'name':'Disposable','path':str(root/'repository')})
    changed = jobs.set_destination({'id':destination['id'],'enabled':False,'customer_visible':False})
    assert changed['enabled'] is False and changed['customer_visible'] is False
    result = jobs.delete_destination({'id':destination['id']})
    assert result == {'id':destination['id'],'status':'configuration_removed','remote_data_deleted':False}
    assert jobs.destinations({})['destinations'] == []


def test_cancel_and_retry_persist_queue_state(environment):
    root, queued = environment
    destination = make_destination(root)
    policy = make_policy(destination)
    original = jobs.queue_policy({'id':policy['id']})['run_ids'][0]
    cancelled = jobs.cancel_run({'id':original})
    assert cancelled['status'] == 'cancelled'
    retried = jobs.retry_run({'id':original})
    assert retried['status'] == 'pending' and retried['trigger'] == 'retry'
    assert len(queued) == 2


def test_account_catalog_distinguishes_attempts_from_usable_points(environment):
    root, _ = environment
    destination = make_destination(root)
    policy = make_policy(destination)
    failed = jobs.queue_policy({'id':policy['id']})['run_ids'][0]
    jobs._update(failed,status='failed',error='fixture failure',completed_at=jobs.utcnow())
    rows = {row['username']:row for row in jobs.account_catalog({})['accounts']}
    assert rows['alpha']['availability'] == 'no_backups'
    assert rows['alpha']['latest_attempt_status'] == 'failed'
    assert rows['alpha']['recovery_point_count'] == 0
    assert rows['bravo']['availability'] == 'not_scheduled'


def test_retention_keeps_recent_daily_weekly_and_monthly_points():
    moments=['2026-09-25T02:00:00Z','2026-09-25T01:00:00Z','2026-09-24T01:00:00Z',
        '2026-09-18T01:00:00Z','2026-08-20T01:00:00Z','2026-07-20T01:00:00Z']
    items=[{'id':str(index),'time':value} for index,value in enumerate(moments)]
    kept=jobs.retained_snapshot_ids(items,{'retention_count':1,'retention_daily':2,
        'retention_weekly':2,'retention_monthly':2})
    assert {'0','2','3','4'} <= kept
    assert '1' not in kept
