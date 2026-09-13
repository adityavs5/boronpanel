import json
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from api.routers import wordpress as api
from api.security import Identity
from daemon import wpmanager as manager
from shared.db import write_session
from shared.models import Account, Domain

@pytest.fixture
def sites(isolated_db,tmp_path):
    with write_session() as s:
        for name in ('alice','bob'):
            a=Account(username=name);s.add(a);s.flush()
            root=tmp_path/name;root.mkdir();(root/'wp-config.php').write_text('<?php')
            (root/'wp-includes').mkdir();(root/'wp-includes/version.php').write_text("<?php $wp_version = '7.0';")
            s.add(Domain(account_id=a.id,domain=name+'.example',docroot=str(root),kind='primary'))
    return tmp_path

def test_inventory_is_account_scoped(sites):
    result=manager.inventory({'username':'alice'})
    assert [x['domain'] for x in result['installs']]==['alice.example']
    assert [x['username'] for x in result['domains']]==['alice']
    assert 'docroot' not in result['installs'][0]
    assert len(manager.inventory({})['installs'])==2

def test_customer_cannot_request_all_installations(monkeypatch):
    customer=Identity(panel_user_id=1,username='alice',role='customer',account_id=1,auth_method='session')
    with pytest.raises(HTTPException):api.manager_inventory(None,customer)

def test_login_checks_account_and_domain_ownership(sites):
    with pytest.raises(RuntimeError,match='not found for account'):
        manager.login({'username':'alice','domain':'bob.example'})

def test_login_token_not_embedded_and_single_use(sites,monkeypatch):
    captured={}
    monkeypatch.setattr(manager.pwd,'getpwnam',lambda user:SimpleNamespace(pw_dir=str(sites/user)))
    def fake_run(argv,**kwargs): captured.update(argv=argv,**kwargs);return SimpleNamespace(ok=True)
    monkeypatch.setattr(manager,'run',fake_run)
    result=manager.login({'username':'alice','domain':'alice.example'})
    payload=json.loads(captured['input_text'])
    assert result['token'] not in payload['code']
    assert result['token'] not in result['url']
    assert 'hash_equals' in payload['code'] and "'.used', 'x'" in payload['code']
    assert "@unlink(__FILE__ . '.used')" not in payload['code']
    assert captured['argv'][:3]==['runuser','-u','alice']
    assert result['expires_in']==90

def test_clone_refuses_overwrite_before_database_allocation(sites,monkeypatch):
    monkeypatch.setattr(manager.wpcli,'ensure_wpcli',lambda:'/fake/wp')
    with pytest.raises(Exception,match='empty'):
        manager.operation({'username':'alice','domain':'alice.example','action':'clone','target_domain':'alice.example','target_path':'wp-includes'})

def test_restore_requires_explicit_confirmation(sites,monkeypatch):
    monkeypatch.setattr(manager.wpcli,'ensure_wpcli',lambda:'/fake/wp')
    with pytest.raises(Exception,match='Confirm'):
        manager.operation({'username':'alice','domain':'alice.example','action':'restore'})

def test_worker_refuses_root_execution():
    import importlib.util,os
    if os.geteuid()!=0:pytest.skip('root-specific guard')
    spec=importlib.util.spec_from_file_location('wpworker','scripts/wordpress_manage.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    with pytest.raises(RuntimeError,match='hosting account'):m.main({})

@pytest.fixture
def worker(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('wpworker','scripts/wordpress_manage.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    monkeypatch.setattr(m.os,'geteuid',lambda:1001)
    monkeypatch.setattr(m.Path,'home',lambda:tmp_path)
    root=tmp_path/'site';root.mkdir();(root/'wp-config.php').write_text('original config');(root/'index.php').write_text('original page')
    calls=[]
    def run(argv,**kw):
        calls.append(argv)
        if 'export' in argv: Path(argv[argv.index('export')+1]).write_text('original database')
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(m.subprocess,'run',run)
    return m,root,{'root':str(root),'php':'php','phar':'wp'},calls

def test_backup_private_archive_excludes_links_and_login_tokens(worker,tmp_path):
    import tarfile
    m,root,c,_=worker
    (root/'outside').symlink_to('/etc/passwd')
    (root/'boron-login-secret.php').write_text('secret')
    result=m.main({**c,'action':'backup'})
    file=next((tmp_path/'.boron-wordpress').glob('*/'+result['name']))
    assert file.stat().st_mode & 0o777 == 0o600
    with tarfile.open(file) as t:
        assert 'database.sql' in t.getnames()
        assert 'files/wp-config.php' in t.getnames()
        assert not any('outside' in n or 'boron-login-' in n for n in t.getnames())

def test_restore_restores_files_and_database_preserving_current_credentials(worker):
    m,root,c,calls=worker
    b=m.main({**c,'action':'backup'})
    (root/'index.php').write_text('changed');(root/'new.php').write_text('new')
    (root/'wp-config.php').write_text('rotated credentials')
    result=m.main({**c,'action':'restore','backup':b['name']})
    assert (root/'index.php').read_text()=='original page'
    assert not (root/'new.php').exists()
    assert (root/'wp-config.php').read_text()=='rotated credentials'
    assert result['safety_backup']!=b['name']
    assert any('import' in argv for argv in calls)

def test_failed_restore_recovers_pre_restore_site(worker,monkeypatch):
    m,root,c,_=worker
    b=m.main({**c,'action':'backup'})
    (root/'index.php').write_text('before restore')
    original=m.subprocess.run;imports=[]
    def failing(argv,**kw):
        if 'import' in argv:
            imports.append(argv)
            if len(imports)==1:return SimpleNamespace(returncode=1,stdout='',stderr='database failure')
        return original(argv,**kw)
    monkeypatch.setattr(m.subprocess,'run',failing)
    with pytest.raises(RuntimeError,match='original website was recovered'):
        m.main({**c,'action':'restore','backup':b['name']})
    assert (root/'index.php').read_text()=='before restore'
    assert len(imports)==2

def test_restore_rejects_archive_path_traversal_before_touching_site(worker,tmp_path):
    import tarfile,io
    m,root,c,_=worker
    b=m.main({**c,'action':'backup'})
    file=next((tmp_path/'.boron-wordpress').glob('*/'+b['name']))
    with tarfile.open(file,'w:gz') as archive:
        info=tarfile.TarInfo('../escaped');info.size=4;archive.addfile(info,io.BytesIO(b'bad!'))
    with pytest.raises(RuntimeError,match='unsafe paths'):
        m.main({**c,'action':'restore','backup':b['name']})
    assert (root/'index.php').read_text()=='original page'
    assert not (tmp_path/'escaped').exists()

def test_login_handoff_scopes_csp_without_relaxing_panel_policy(sites,monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    from api.security import get_identity
    admin=Identity(panel_user_id=1,username='admin',role='admin',account_id=None,auth_method='session')
    app.dependency_overrides[get_identity]=lambda:admin
    monkeypatch.setattr(api,'call_daemon',lambda *args,**kwargs:{'url':'https://alice.example/boron-login-test.php','token':'private-token'})
    try:
        client=TestClient(app)
        r=client.post('/api/v1/accounts/alice/domains/alice.example/wordpress/login/open',data={'path':''})
        assert r.status_code==200
        assert 'form-action https://alice.example' in r.headers['content-security-policy']
        assert "script-src 'nonce-" in r.headers['content-security-policy']
        assert r.headers['cache-control']=='no-store'
        assert r.headers['referrer-policy']=='no-referrer'
        assert 'private-token' in r.text
        assert "form-action 'self'" in client.get('/app').headers['content-security-policy']
    finally:app.dependency_overrides.pop(get_identity,None)

def test_admin_inventory_includes_suspended_sites_but_no_install_destinations(sites):
    from sqlalchemy import select
    with write_session() as s:s.scalar(select(Account).where(Account.username=='alice')).status='suspended'
    data=manager.inventory({})
    assert next(i for i in data['installs'] if i['username']=='alice')['account_status']=='suspended'
    assert not any(d['username']=='alice' for d in data['domains'])

def test_clone_allows_empty_provisioning_folders(sites,monkeypatch):
    root=sites/'clone';root.mkdir();(root/'.well-known').mkdir();(root/'error_pages').mkdir()
    from sqlalchemy import select
    with write_session() as s:
        a=s.scalar(select(Account).where(Account.username=='alice'))
        s.add(Domain(account_id=a.id,domain='clone.example',docroot=str(root),kind='addon'))
    monkeypatch.setattr(manager.wpcli,'ensure_wpcli',lambda:'/fake/wp')
    monkeypatch.setattr(manager.wordpress,'_allocate_database',lambda *a:({'db_name':'alice_wp','db_user':'alice_wp','password':'secret'},'wp'))
    capture={}
    def submit(*args,**kwargs):capture.update(kwargs);return {'id':1,'status':'pending'}
    monkeypatch.setattr(manager.cmdjobs,'submit',submit)
    result=manager.operation({'username':'alice','domain':'alice.example','action':'clone','target_domain':'clone.example'})
    assert result['status']=='pending'
    assert json.loads(capture['input_text'])['target']==str(root)
    assert callable(capture['on_failure'])

def test_update_cannot_overlap_backup(sites):
    from shared.models import CommandRun
    from sqlalchemy import select
    with write_session() as s:
        a=s.scalar(select(Account).where(Account.username=='alice'))
        s.add(CommandRun(account_id=a.id,kind='wpmanager',target=str(sites/'alice'),command_display='WordPress backup',status='running'))
    with pytest.raises(Exception,match='already running'):
        manager.wpcli.run_wpcli({'username':'alice','domain':'alice.example','action':'core_update'})

def test_parent_backup_and_restore_preserve_independent_subfolder_site(worker,tmp_path):
    import tarfile
    m,root,c,_=worker
    child=root/'blog';child.mkdir();(child/'wp-config.php').write_text('child database');(child/'index.php').write_text('independent site')
    b=m.main({**c,'action':'backup'})
    file=next((tmp_path/'.boron-wordpress').glob('*/'+b['name']))
    with tarfile.open(file) as t:assert not any(n.startswith('files/blog') for n in t.getnames())
    (child/'index.php').write_text('newer child content')
    m.main({**c,'action':'restore','backup':b['name']})
    assert (child/'index.php').read_text()=='newer child content'
    assert (child/'wp-config.php').read_text()=='child database'

def test_clone_into_staging_folder_does_not_recursively_copy_itself(worker,monkeypatch):
    m,root,c,calls=worker
    original=m.subprocess.run
    def run(argv,**kw):
        if 'table_prefix' in argv:return SimpleNamespace(returncode=0,stdout='wp_',stderr='')
        if 'home' in argv and 'get' in argv:return SimpleNamespace(returncode=0,stdout='https://example.com',stderr='')
        return original(argv,**kw)
    monkeypatch.setattr(m.subprocess,'run',run)
    result=m.main({**c,'action':'clone','target':str(root/'staging'),'url':'https://example.com/staging','database':{'db_name':'clone_db','db_user':'clone_user','password':'secret'}})
    assert (root/'staging/index.php').read_text()=='original page'
    assert not (root/'staging/staging').exists()
    assert "clone_db" in (root/'staging/wp-config.php').read_text()
    assert (root/'wp-config.php').read_text()=='original config'
    assert result['url']=='https://example.com/staging'

def test_inventory_activity_never_reveals_install_password(sites):
    from shared.models import WordPressJob
    from sqlalchemy import select
    with write_session() as s:
        a=s.scalar(select(Account).where(Account.username=='alice'))
        s.add(WordPressJob(account_id=a.id,domain='alice.example',status='completed',admin_password='never-expose-this',admin_user='siteadmin'))
    result=manager.inventory({'username':'alice'})
    assert result['activity'][0]['status']=='completed'
    assert 'never-expose-this' not in json.dumps(result)
    assert not manager.inventory({'username':'bob'})['activity']

def test_install_cannot_overlap_management_and_management_cannot_overlap_install(sites):
    from shared.models import CommandRun,WordPressJob
    from sqlalchemy import select
    with write_session() as s:
        a=s.scalar(select(Account).where(Account.username=='alice'))
        job=CommandRun(account_id=a.id,kind='wpmanager',target=str(sites/'alice'),command_display='Clone',status='running');s.add(job);s.flush();job_id=job.id
    with pytest.raises(Exception,match='already running'):
        manager.wordpress.trigger_install({'username':'alice','domain':'alice.example'})
    with write_session() as s:
        s.get(CommandRun,job_id).status='completed'
        a=s.scalar(select(Account).where(Account.username=='alice'))
        s.add(WordPressJob(account_id=a.id,domain='alice.example',status='running'))
    with pytest.raises(Exception,match='already running'):
        manager.operation({'username':'alice','domain':'alice.example','action':'backup'})


def test_soft_remove_hides_record_and_scan_rediscovers(sites, monkeypatch):
    monkeypatch.setattr(manager, '_account_command', lambda user, root, args:
        'http://alice.example' if args[0] == 'option' else 'alice_wp')
    p = {'username':'alice','domain':'alice.example'}
    assert manager.remove({**p,'mode':'soft'})['status'] == 'removed'
    assert manager.inventory({'username':'alice'})['installs'] == []
    assert (sites/'alice/wp-config.php').exists()
    assert manager.scan({'username':'alice'})['found'] == 1
    site = manager.inventory({'username':'alice'})['installs'][0]
    assert site['url'] == 'http://alice.example'
    assert site['scanned_at']


def test_refresh_rejects_foreign_url(sites, monkeypatch):
    monkeypatch.setattr(manager, '_account_command', lambda *args:'https://bob.example')
    with pytest.raises(Exception, match='this domain'):
        manager.refresh_site({'username':'alice','domain':'alice.example'})


def test_hard_remove_requires_confirmation_and_owned_database(sites, monkeypatch):
    p = {'username':'alice','domain':'alice.example','mode':'hard'}
    with pytest.raises(Exception, match='exact installation address'):
        manager.remove(p)
    monkeypatch.setattr(manager, '_database_at', lambda *args:'unowned_database')
    with pytest.raises(Exception, match='not registered'):
        manager.remove({**p,'confirmation':'alice.example'})
    assert (sites/'alice/wp-config.php').exists()


def test_hard_remove_refuses_shared_database(sites, monkeypatch):
    from shared.models import DatabaseGrant
    from sqlalchemy import select
    with write_session() as s:
        account = s.scalar(select(Account).where(Account.username=='alice'))
        s.add(DatabaseGrant(account_id=account.id, db_name='alice_wp', db_user='alice_wp'))
    nested = sites/'alice/blog';nested.mkdir();(nested/'wp-config.php').write_text('<?php')
    monkeypatch.setattr(manager, '_database_at', lambda *args:'alice_wp')
    with pytest.raises(Exception, match='shares this database'):
        manager.remove({'username':'alice','domain':'alice.example','mode':'hard','confirmation':'alice.example'})
    assert (nested/'wp-config.php').exists()


def test_remove_worker_rollback_and_commit_preserve_other_sites(worker, monkeypatch):
    m,root,c,_ = worker
    monkeypatch.setattr(m.subprocess,'run',lambda *args,**kw:SimpleNamespace(returncode=0,stdout='alice_wp',stderr=''))
    nested=root/'blog';nested.mkdir();(nested/'wp-config.php').write_text('independent site')
    cert=root/'.well-known';cert.mkdir();(cert/'token').write_text('validation')
    c={**c,'database_name':'alice_wp','removal_id':'a'*32}
    m.main({**c,'action':'remove_prepare'})
    assert not (root/'wp-config.php').exists()
    assert (nested/'wp-config.php').exists() and (cert/'token').exists()
    m.main({**c,'action':'remove_rollback'})
    assert (root/'index.php').read_text()=='original page'
    m.main({**c,'action':'remove_prepare'})
    m.main({**c,'action':'remove_commit'})
    assert not (root/'wp-config.php').exists()
    assert (nested/'wp-config.php').read_text()=='independent site'


def test_remove_worker_database_change_leaves_files_intact(worker):
    m,root,c,_=worker
    with pytest.raises(RuntimeError, match='database changed'):
        m.main({**c,'action':'remove_prepare','database_name':'expected','removal_id':'b'*32})
    assert (root/'wp-config.php').read_text()=='original config'


def test_customer_cannot_scan_other_accounts():
    customer=Identity(panel_user_id=1,username='alice',role='customer',account_id=1,auth_method='session')
    with pytest.raises(HTTPException):api.scan_installations(api.ScanBody(),customer)
    with pytest.raises(HTTPException):api.scan_installations(api.ScanBody(username='bob'),customer)


@pytest.mark.parametrize('database_failure', [False, True])
def test_hard_removal_commits_only_after_database_cleanup(sites, monkeypatch, database_failure):
    from shared.models import DatabaseGrant
    from sqlalchemy import select
    with write_session() as s:
        a=s.scalar(select(Account).where(Account.username=='alice'))
        grant=DatabaseGrant(account_id=a.id,db_name='alice_wp',db_user='alice_wp')
        s.add(grant);s.flush();grant_id=grant.id
    monkeypatch.setattr(manager,'_database_at',lambda *args:'alice_wp')
    monkeypatch.setattr(manager.wpcli,'ensure_wpcli',lambda:'/fake/wp')
    events=[]
    def drop_database(name):
        events.append('database')
        if database_failure: raise RuntimeError('database unavailable')
    monkeypatch.setattr(manager.wordpress.handlers_database.mariadb,'drop_database',drop_database)
    monkeypatch.setattr(manager.wordpress.handlers_database.mariadb,'drop_db_user',lambda name:events.append('user'))
    monkeypatch.setattr(manager,'_removal_worker',lambda u,r,c:events.append(c['action']))
    def submit(*args,**kw):
        try: kw['on_success']()
        except Exception:
            kw['on_failure']()
            raise
        return {'status':'completed'}
    monkeypatch.setattr(manager.cmdjobs,'submit',submit)
    p={'username':'alice','domain':'alice.example','mode':'hard','confirmation':'alice.example'}
    if database_failure:
        with pytest.raises(RuntimeError,match='database unavailable'):manager.remove(p)
        assert events==['database','remove_rollback']
        assert len(manager.inventory({'username':'alice'})['installs'])==1
    else:
        assert manager.remove(p)['status']=='completed'
        assert events==['database','user','remove_commit']
        assert manager.inventory({'username':'alice'})['installs']==[]
    with write_session() as s:
        assert (s.get(DatabaseGrant,grant_id) is not None)==database_failure


def test_inventory_does_not_use_previous_domain_owners_metadata(sites):
    from sqlalchemy import select
    from shared.models import WordPressInstall
    with write_session() as s:
        bob=s.scalar(select(Account).where(Account.username=='bob'))
        s.add(WordPressInstall(account_id=bob.id,domain='alice.example',path='',
            admin_user='previous_owner_admin',db_name='bob_old',db_user='bob_old',wp_version='6.0'))
    for scope in ({}, {'username':'alice'}):
        site=next(i for i in manager.inventory(scope)['installs'] if i['domain']=='alice.example')
        assert site['admin_user']==''
        assert site['installed_at'] is None
