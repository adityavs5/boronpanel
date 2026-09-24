from pathlib import Path
import json
import pytest
from daemon import directadmin_fidelity as da
from daemon import cpanel_import as ci
from shared.validation import ValidationError

HASH = '$6$salt$' + 'a' * 86


@pytest.fixture(autouse=True)
def installed_runtimes(monkeypatch):
    monkeypatch.setattr(da, 'available_php_versions', lambda: set(da.settings.php_versions))
    monkeypatch.setattr(da.os, 'access', lambda *args: True)


def native(tmp_path):
    source = tmp_path / 'source'
    meta = source / 'backup' / 'example.com'
    meta.mkdir(parents=True)
    (source / 'backup/user.conf').write_text('username=alice\ndomain=example.com\n')
    (meta / 'domain.conf').write_text('php1_select=2\n')
    web = source / 'domains/example.com/public_html'
    web.mkdir(parents=True)
    (web / 'index.php').write_text('<?php echo "hello";')
    output = tmp_path / 'normalized'
    output.mkdir()
    return source, meta, web, output


def normalize(source, output):
    ci._normalize_directadmin_archive(source, output, {'example.com': {'selected': '8.2', 'slots': {'1': '8.1', '2': '8.2'}}})
    return json.loads((output / 'directadmin-manifest.json').read_text())


def test_native_domain_subdomain_docroots_and_php_slots(tmp_path):
    source, meta, web, output = native(tmp_path)
    (meta / 'subdomain.list').write_text('shop\n')
    (meta / 'domain.subdomains.docroot.override').write_text('shop=public_html=/domains/example.com/public_html/store&php1_select=1\n')
    (web / 'store').mkdir()
    (web / 'store/index.php').write_text('<?php echo 1;')
    manifest = normalize(source, output)
    assert not manifest['blockers']
    assert manifest['domains'][0]['php_version'] == '8.2'
    assert manifest['domains'][1] == {'domain': 'shop.example.com', 'kind': 'subdomain', 'docroot': 'domains/example.com/public_html/store', 'php_version': '8.1'}
    info = ci._parse_account_info(output)
    assert {'domain': 'shop.example.com', 'kind': 'subdomain'} in ci._parse_domains(output, info)


def test_mail_preserves_hash_empty_mailbox_quota_and_forwards(tmp_path):
    source, meta, web, output = native(tmp_path)
    email = meta / 'email'; email.mkdir()
    (email / 'passwd').write_text('sales:' + HASH + ':1001:1001::/unused:/bin/false\n')
    (email / 'quota').write_text('sales:524288000\n')
    (email / 'aliases').write_text('sales:sales\ninfo:sales@example.com\n*:sales@example.com\n')
    m = normalize(source, output)
    assert not m['blockers']
    assert m['mailboxes'] == [{'domain': 'example.com', 'local_part': 'sales', 'password_hash': '{CRYPT}' + HASH, 'quota_mb': 500}]
    assert (output / 'homedir/mail/example.com/sales/new').is_dir()
    assert m['forwards'][0]['destination'] == 'sales@example.com'
    assert m['catchalls'][0]['destination'] == 'sales@example.com'


def test_missing_php_version_is_not_replaced_with_default(tmp_path):
    source, meta, web, output = native(tmp_path)
    ci._normalize_directadmin_archive(source, output)
    m = json.loads((output / 'directadmin-manifest.json').read_text())
    assert any('PHP version is unknown' in b for b in m['blockers'])


def test_unavailable_php_version_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(da.settings, 'php_versions', ('8.3',))
    source, meta, web, output = native(tmp_path)
    assert any('PHP 8.2 is not installed' in b for b in normalize(source, output)['blockers'])


@pytest.mark.parametrize('path', ['/etc/passwd', '../../root', '.ssh', '/home/bob/site', 'domains/../../etc'])
def test_paths_cannot_escape_or_expose_protected_directories(path):
    with pytest.raises(ValidationError): da.relative_path(path, 'alice')


@pytest.mark.parametrize('value', ['plaintext', '$6$bad', '{PLAIN}secret', '!locked'])
def test_unknown_mail_hash_does_not_silently_reset_password(value):
    with pytest.raises(ValidationError): da.password_hash(value)


def test_supported_passenger_node_app_inventory(tmp_path):
    source, meta, web, output = native(tmp_path)
    (web / '.htaccess').write_text('PassengerAppRoot /home/alice/domains/example.com/public_html\nPassengerAppType node\nPassengerNodejs /opt/alt/alt-nodejs22/root/usr/bin/node\nPassengerStartupFile app.js\nSetEnv NODE_ENV production\n')
    (web / 'app.js').write_text('require("http").createServer(()=>{}).listen(process.env.PORT)')
    (web / 'package.json').write_text('{}')
    m = normalize(source, output)
    assert not m['blockers']
    assert m['apps'][0]['version'] == '22'
    assert m['apps'][0]['env'] == {'NODE_ENV': 'production'}


def test_path_mounted_app_is_reported_not_silently_served_at_root(tmp_path):
    source, meta, web, output = native(tmp_path)
    (web / '.htaccess').write_text('PassengerAppType node\nPassengerBaseURI /app\nPassengerNodejs /opt/alt/alt-nodejs22/root/usr/bin/node\n')
    m = normalize(source, output)
    assert any('path-mounted' in b for b in m['blockers'])


def test_native_dns_and_cert_paths_are_recognized(tmp_path):
    source, meta, web, output = native(tmp_path)
    (meta / 'example.com.db').write_text('zone fixture')
    (meta / 'domain.cert').write_text('cert fixture')
    (meta / 'domain.key').write_text('key fixture')
    normalize(source, output)
    assert (output / 'dnszones/example.com.db').read_text() == 'zone fixture'
    assert (output / 'ssl/keys/example.com.key').read_text() == 'key fixture'


def test_cloudlinux_inventory_is_scoped_and_preserves_environment_and_state():
    data = {'result': 'success', 'available_versions': {'22.10.0': {'users': {
        'alice': {'applications': {'apps/shop': {'domain': 'example.com', 'app_uri': '/', 'startup_file': 'server.js', 'app_status': 'stopped', 'env_vars': {'SECRET': 'private-value'}}}},
        'bob': {'applications': {'other': {'domain': 'other.example.com'}}},
    }}}}
    apps = da.selector_applications(data, 'alice', 'nodejs')
    assert len(apps) == 1
    assert apps[0]['env'] == {'SECRET': 'private-value'}
    assert apps[0]['enabled'] is False
    assert apps[0]['version'] == '22'


def test_python_application_inventory_checks_exact_minor_version(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(da.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='3.12\n'))
    source, meta, web, output = native(tmp_path)
    (web / 'passenger_wsgi.py').write_text('application = lambda environ,start: []')
    (web / 'requirements.txt').write_text('Flask==3.0.0\n')
    (web / '.htaccess').write_text('PassengerAppType wsgi\nPassengerPython /home/alice/virtualenv/site/3.11/bin/python\n')
    m = normalize(source, output)
    assert any('Python 3.11 does not match destination 3.12' in x for x in m['blockers'])


def test_mailbox_data_without_credentials_is_not_silently_reset(tmp_path):
    source, meta, web, output = native(tmp_path)
    box=source/'imap/example.com/lost/Maildir/new'
    box.mkdir(parents=True)
    (box/'message').write_text('test message')
    m=normalize(source, output)
    assert any('mail data exists without credentials' in x for x in m['blockers'])


def test_dns_import_preserves_multiple_record_values(monkeypatch,tmp_path):
    calls=[]
    monkeypatch.setattr(ci.handlers_dns,'create_zone',lambda p:None)
    monkeypatch.setattr(ci,'_parse_bind_zone_records',lambda *a:[{'subdomain':'@','type':'MX','value':'10 mx1.example.com.'},{'subdomain':'@','type':'MX','value':'20 mx2.example.com.'}])
    monkeypatch.setattr(ci.handlers_dns,'set_record',lambda p:calls.append(p))
    ci._import_dns_zone('alice','example.com',tmp_path/'zone')
    assert len(calls)==1
    assert calls[0]['values']==['10 mx1.example.com.','20 mx2.example.com.']


def test_dns_import_failure_cannot_be_reported_as_success(monkeypatch,tmp_path):
    monkeypatch.setattr(ci.handlers_dns,'create_zone',lambda p:None)
    monkeypatch.setattr(ci,'_parse_bind_zone_records',lambda *a:[{'subdomain':'@','type':'TXT','value':'data'}])
    monkeypatch.setattr(ci.handlers_dns,'set_record',lambda p:(_ for _ in ()).throw(RuntimeError('failed write')))
    with pytest.raises(RuntimeError):ci._import_dns_zone('alice','example.com',tmp_path/'zone')


def test_analysis_request_keeps_flag():
    from api.routers.cpanel_import import DirectAdminImport
    params=DirectAdminImport(remote={'host':'example.com','password':'test'},remote_user='alice',username='alice',preflight_only=True)
    assert params.model_dump()['preflight_only'] is True


@pytest.mark.parametrize('analyze_only,blocked',[(True,False),(False,True)])
def test_da_preflight_never_creates_account_for_analysis_or_blockers(isolated_db,tmp_path,monkeypatch,analyze_only,blocked):
    from shared.db import write_session
    from shared.models import CpanelImportJob,Account
    from sqlalchemy import select
    from daemon import import_compat
    source,meta,web,normalized=native(tmp_path)
    (meta/'domain.conf').write_text('php_version=7.4\n' if blocked else 'php_version=8.2\n')
    archive=tmp_path/'source.tar.gz'
    import tarfile
    with tarfile.open(archive,'w:gz') as tf:
        for path in source.iterdir():tf.add(path,arcname=path.name)
    monkeypatch.setattr(ci.settings,'cpanel_import_staging_dir',str(tmp_path/'staging'))
    monkeypatch.setattr(ci.handlers_account,'create_account',lambda p:pytest.fail('preflight must never create an account'))
    monkeypatch.setattr(import_compat,'preflight_dumps',lambda *args:[])
    with write_session() as db:
        job=CpanelImportJob(username='alice',panel='directadmin',source='upload',source_ref=str(archive),status='pending',results=[])
        db.add(job);db.flush();job_id=job.id
    ci._run_import_job(job_id,{'source':'upload','source_ref':str(archive),'preflight_only':analyze_only})
    with write_session() as db:
        job=db.get(CpanelImportJob,job_id)
        assert job.status==('failed' if blocked else 'completed')
        assert db.scalar(select(Account)) is None
        assert job.results


def test_composer_extension_requirements_checked_before_restore(tmp_path,monkeypatch):
    source,meta,web,out=native(tmp_path)
    (web/'composer.json').write_text(json.dumps({'require':{'php':'^8.2','ext-imagick':'*'}}))
    monkeypatch.setattr(da,'php_extensions',lambda version:{'mysqli','json'})
    assert any('lacks declared extensions: imagick' in b for b in normalize(source,out)['blockers'])


def test_account_node_selector_layout_from_readonly_source(tmp_path):
    source, meta, web, output = native(tmp_path)
    home = tmp_path / 'source-home'
    app = home / 'nodeapps/backend'; app.mkdir(parents=True)
    (app / 'server.js').write_text('require("http").createServer(()=>{}).listen(process.env.PORT)')
    (app / 'package.json').write_text('{"engines":{"node":">=18.0.0"}}')
    selector = home / '.cl.selector'; selector.mkdir()
    (selector / 'node-selector.json').write_text(json.dumps({'nodeapps/backend': {
        'app_mode': 'production', 'app_status': 'started', 'app_uri': '',
        'config_files': [], 'domain': 'example.com', 'env_vars': {},
        'nodejs_version': '20', 'startup_file': 'server.js',
    }}))
    import tarfile
    with tarfile.open(source / 'backup/home.tar', 'w') as archive:
        for child in home.iterdir(): archive.add(child, arcname=child.name)
    m = normalize(source, output)
    assert not m['blockers']
    assert m['apps'][0]['root'] == 'nodeapps/backend'
    assert m['apps'][0]['version'] == '20'
    assert m['apps'][0]['env'] == {'NODE_ENV': 'production'}
    assert m['apps'][0]['startup'] == 'server.js'


def test_selector_unknown_state_is_not_silently_stopped():
    with pytest.raises(ValidationError, match='state'):
        da.selector_file_applications({'app': {'app_status': 'unknown'}}, 'alice')


@pytest.mark.parametrize('has_message', [False, True])
def test_empty_system_maildir_does_not_block_but_real_messages_do(tmp_path, has_message):
    source, meta, web, output = native(tmp_path)
    (meta / 'aliases').write_text('alice:alice\n')
    maildir = tmp_path / 'source-mail/Maildir/new'; maildir.mkdir(parents=True)
    if has_message: (maildir / 'message').write_text('test message')
    import tarfile
    with tarfile.open(source / 'backup/home.tar', 'w') as archive:
        archive.add(maildir.parent, arcname='Maildir')
    m = normalize(source, output)
    assert any('system mailbox' in b for b in m['blockers']) is has_message


@pytest.mark.parametrize('host,db,expected', [('localhost','alice_shop',True), ('remote.example.com','alice_shop',False), ('localhost','missing',False)])
def test_verified_node_database_layout_maps_only_local_included_database(tmp_path,host,db,expected):
    app = tmp_path / 'homedir/nodeapps/backend'; (app / 'src/config').mkdir(parents=True)
    (app / 'src/config/database.js').write_text("const mysql = require('mysql2/promise'); module.exports=mysql.createPool({host:process.env.DB_HOST,port:process.env.DB_PORT,user:process.env.DB_USER,password:process.env.DB_PASSWORD,database:process.env.DB_NAME});")
    (app / '.env').write_text(f'DB_HOST={host}\nDB_NAME={db}\nDB_PASSWORD=private-test-value\n')
    manifest={'apps':[{'root':'nodeapps/backend','domain':'example.com','kind':'node','env':{}}],'blockers':[]}
    da.map_application_databases(tmp_path,manifest,{'alice_shop'})
    assert bool(manifest['blockers']) is not expected
    if expected: assert manifest['apps'][0]['database_name']=='alice_shop'
    assert 'private-test-value' not in str(manifest)


@pytest.mark.parametrize('content', ['DB_NAME=${OTHER}', 'DB_NAME=one\nDB_NAME=two', 'DB_NAME="unterminated'])
def test_ambiguous_dotenv_is_not_evaluated(tmp_path,content):
    path=tmp_path/'.env';path.write_text(content)
    with pytest.raises(ValidationError): da.dotenv_values(path)
