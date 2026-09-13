"""WordPress inventory and account-isolated management operations."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import time
import re
import threading
from sqlalchemy import select
from daemon import cmdjobs, wordpress, wpcli
from daemon.procutil import run
from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain, WordPressInstall, WordPressJob, CommandRun, WordPressSiteState, DatabaseGrant, utcnow
from shared.validation import ValidationError, validate_username, validate_domain

_operation_lock = threading.RLock()

WORKER = str(Path(__file__).resolve().parent.parent / 'scripts' / 'wordpress_manage.py')


def inventory(params):
    username = params.get('username')
    with write_session() as s:
        query = select(Account).where(Account.status != 'terminated')
        if username: query = query.where(Account.username == validate_username(username))
        rows = s.scalars(query).all()
        accounts = [(a.id, a.username) for a in rows]
        statuses = {a.username: a.status for a in rows}
        domains = s.scalars(select(Domain).where(Domain.account_id.in_([a[0] for a in accounts]))).all() if accounts else []
        choices = [{'username': next(n for i,n in accounts if i == d.account_id), 'domain': d.domain, 'kind': d.kind} for d in domains if d.kind != 'alias' and statuses[next(n for i,n in accounts if i == d.account_id)] == 'active']
        states = {(r.account_id, r.domain, r.path): (r.hidden, r.site_url, r.scanned_at.isoformat() if r.scanned_at else None) for r in s.scalars(select(WordPressSiteState).where(WordPressSiteState.account_id.in_([a[0] for a in accounts]))).all()}
        records = {(r.account_id, r.domain, r.path): (r.admin_user, r.installed_at.isoformat()) for r in s.scalars(select(WordPressInstall).where(WordPressInstall.account_id.in_([a[0] for a in accounts]))).all()}
        account_ids = [a[0] for a in accounts]
        recent_installs = s.scalars(select(WordPressJob).where(WordPressJob.account_id.in_(account_ids)).order_by(WordPressJob.id.desc()).limit(10)).all() if account_ids else []
        recent_commands = s.scalars(select(CommandRun).where(CommandRun.account_id.in_(account_ids), CommandRun.kind.in_(['wpmanager','wpcli'])).order_by(CommandRun.id.desc()).limit(10)).all() if account_ids else []
        activity = [{'id': 'install-'+str(j.id), 'label': 'Install WordPress: '+j.domain, 'status': j.status, 'message': j.progress_message, 'error': j.error} for j in recent_installs]
        activity += [{'id': 'manage-'+str(j.id), 'label': j.command_display, 'status': j.status, 'error': j.error} for j in recent_commands]
    installs, errors = [], []
    for account_id, name in accounts:
        try:
            for item in wpcli.detect_installs({'username': name})['installs']:
                hidden, site_url, scanned_at = states.get((account_id, item['domain'], item['path']), (False, None, None))
                if hidden: continue
                admin_user, installed_at = records.get((account_id, item['domain'],item['path']), ('',None))
                item.update(username=name, account_status=statuses[name], admin_user=admin_user, installed_at=installed_at,
                            url=site_url or 'https://' + item['domain'] + ('/' + item['path'] if item['path'] else ''), scanned_at=scanned_at)
                item.pop('docroot', None)
                installs.append(item)
        except OSError:
            errors.append({'username': name, 'message': 'Could not scan this account. Try refreshing.'})
    return {'installs': installs, 'domains': choices, 'errors': errors, 'activity': activity}


def _site(p):
    username = validate_username(p['username'])
    domain = validate_domain(p['domain'])
    cmdjobs._account(username)  # active account required
    root = wpcli._resolve_install_dir(username, domain, p.get('path', ''))
    return username, domain, root


def _operation(p):
    username, domain, root = _site(p)
    action = p['action']
    if action not in ('backup','backups','restore','clone'): raise ValidationError('Unsupported WordPress operation')
    c = {'root': root, 'action': action, 'php': settings.php_cli_bin, 'phar': wpcli.ensure_wpcli(), 'db_host': 'localhost:' + settings.mariadb_socket}
    if action == 'restore':
        if not p.get('confirm'): raise ValidationError('Confirm restoring this site')
        c['backup'] = p.get('backup', '')
    cleanup = None
    if action == 'clone':
        if p.get('target_path') and not re.fullmatch(r'[A-Za-z0-9_-]+', p['target_path']):
            raise ValidationError('Use a single folder name for the clone')
        target_domain = validate_domain(p['target_domain'])
        _, docroot = wordpress._account_and_domain(username, target_domain)
        target = wordpress._install_target(docroot, p.get('target_path',''))
        real = os.path.realpath(target)
        original = os.path.realpath(root)
        if real == original or original.startswith(real + '/') or (real.startswith(original + '/') and os.path.dirname(real) != original):
            raise ValidationError('Choose a separate destination, not inside the source site')
        if os.path.exists(real) and any(name != '.well-known' and not (name == 'error_pages' and os.path.isdir(os.path.join(real,name)) and not os.listdir(os.path.join(real,name))) for name in os.listdir(real)): raise ValidationError('The clone destination must be empty')
        grant, suffix = wordpress._allocate_database(username, p.get('target_path',''))
        def cleanup():
            wordpress.handlers_database.drop_database({'username': username, 'name': suffix})
        c.update(target=target, database=grant, url='https://' + target_domain + ('/' + p['target_path'].strip('/') if p.get('target_path') else ''))
    # The account worker owns every file operation; no customer PHP is run as root.
    try:
        return cmdjobs.submit(username, 'wpmanager', root, ['/usr/bin/python3', WORKER],
                              f'WordPress {action}: {domain}', input_text=json.dumps(c), timeout=1800,
                              **({'on_failure': cleanup} if cleanup else {}))
    except Exception:
        if cleanup: cleanup()
        raise



def login(p):
    username, domain, root = _site(p)
    token = secrets.token_urlsafe(32)
    filename = 'boron-login-' + secrets.token_hex(16) + '.php'
    digest = hashlib.sha256(token.encode()).hexdigest()
    bridge = '''<?php
header('Cache-Control: no-store'); header('Referrer-Policy: no-referrer');
if ($_SERVER['REQUEST_METHOD'] !== 'POST' || time() > EXPIRES || !hash_equals('DIGEST', hash('sha256', $_POST['token'] ?? ''))) { http_response_code(403); exit('This login link expired. Return to WordPress Manager and try again.'); }
$claim = @fopen(__FILE__ . '.used', 'x'); if (!$claim) { http_response_code(403); exit('Login already used.'); }
fclose($claim); @unlink(__FILE__);
require_once __DIR__ . '/wp-load.php';
$users = get_users(['role'=>'administrator','number'=>1,'orderby'=>'ID','order'=>'ASC']);
if (!$users) { http_response_code(403); exit('No administrator found.'); }
wp_set_current_user($users[0]->ID); wp_set_auth_cookie($users[0]->ID, false, is_ssl());
wp_safe_redirect(admin_url()); exit;
'''.replace('EXPIRES',str(int(time.time())+90)).replace('DIGEST',digest)
    code = """import json,os,pathlib,sys,time
 d=json.load(sys.stdin)
 p=pathlib.Path(d['root'])
 for old in p.glob('boron-login-*'):
     if old.name.startswith('boron-login-') and time.time()-old.lstat().st_mtime>600:
         if old.is_file() or old.is_symlink(): old.unlink(missing_ok=True)
 f=p/d['name']
 fd=os.open(f,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o640)
 os.write(fd,d['code'].encode())
 os.close(fd)
""".replace('\n ', '\n')

    pw = pwd.getpwnam(username)
    result = run(['runuser','-u',username,'--','env',f'HOME={pw.pw_dir}','/usr/bin/python3','-c',code],
                 input_text=json.dumps({'root':root,'name':filename,'code':bridge}), timeout=15)
    if not result.ok: raise RuntimeError('Could not create a secure login link')
    path = p.get('path','').strip('/')
    with write_session() as session:
        account = cmdjobs._account(username)
        state = session.scalar(select(WordPressSiteState).where(
            WordPressSiteState.account_id == account.id, WordPressSiteState.domain == domain,
            WordPressSiteState.path == path))
        site_url = state.site_url if state and state.site_url else 'https://' + domain + ('/'+path if path else '')
    from urllib.parse import urlsplit
    parsed = urlsplit(site_url)
    # The bridge lives in the physical installation folder, even if WordPress
    # has a separately configured homepage URL.
    origin = parsed.scheme + '://' + parsed.netloc
    return {'url': origin + '/' + (path+'/' if path else '') + filename, 'token': token, 'expires_in': 90}


def ensure_idle(username):
    account = cmdjobs._account(validate_username(username))
    with write_session() as session:
        command = session.scalar(select(CommandRun).where(CommandRun.account_id == account.id,
            CommandRun.kind.in_(['wpmanager', 'wpcli']), CommandRun.status.in_(['pending', 'running'])))
        install = session.scalar(select(WordPressJob).where(WordPressJob.account_id == account.id,
            WordPressJob.status.in_(['pending', 'running'])))
        if command or install:
            raise ValidationError('A WordPress operation is already running for this account. Wait for it to finish.')


def operation(p):
    with _operation_lock:
        ensure_idle(p['username'])
        return _operation(p)


def _state(session, account_id, domain, path):
    row = session.scalar(select(WordPressSiteState).where(WordPressSiteState.account_id == account_id,
        WordPressSiteState.domain == domain, WordPressSiteState.path == path))
    if row is None:
        row = WordPressSiteState(account_id=account_id, domain=domain, path=path)
        session.add(row)
    return row


def _account_command(username, root, arguments):
    pw = pwd.getpwnam(username)
    result = run(['runuser', '-u', username, '--', 'env', f'HOME={pw.pw_dir}',
        settings.php_cli_bin, wpcli.ensure_wpcli(), f'--path={root}', '--no-color', *arguments], timeout=45)
    if not result.ok:
        raise ValidationError('Could not inspect WordPress. Check its configuration and database connection.')
    return result.stdout.strip()


def _database_at(username, root):
    name = _account_command(username, root, ['config', 'get', 'DB_NAME'])
    if not re.fullmatch(r'[A-Za-z0-9_]{1,64}', name):
        raise ValidationError('Unsupported WordPress database name')
    return name


def refresh_site(p):
    from urllib.parse import urlsplit
    with _operation_lock:
        username, domain, root = _site(p)
        ensure_idle(username)
        account = cmdjobs._account(username)
        path = p.get('path', '').strip('/')
        url = _account_command(username, root, ['option', 'get', 'siteurl'])
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or parsed.hostname not in (domain, 'www.'+domain) or parsed.username or parsed.password or parsed.port not in (None,80,443) or parsed.query or parsed.fragment:
            raise ValidationError('WordPress site URL must use this domain or its www alias over HTTP or HTTPS')
        database = _database_at(username, root)
        db_user = _account_command(username, root, ['config', 'get', 'DB_USER'])
        with write_session() as session:
            state = _state(session, account.id, domain, path)
            state.hidden = False
            state.site_url = url.rstrip('/')
            state.scanned_at = utcnow()
            record = session.scalar(select(WordPressInstall).where(WordPressInstall.account_id == account.id,
                WordPressInstall.domain == domain, WordPressInstall.path == path))
            if record is None:
                record = WordPressInstall(account_id=account.id, domain=domain, path=path,
                    db_name=database, db_user=db_user[:64], admin_user='', wp_version='')
                session.add(record)
            record.db_name, record.db_user = database, db_user[:64]
            record.wp_version = wpcli._wp_version_at(root) or ''
        return {'status': 'refreshed', 'domain': domain, 'path': path, 'url': url.rstrip('/')}


def scan(p):
    with write_session() as session:
        query = select(Account).where(Account.status == 'active')
        if p.get('username'): query = query.where(Account.username == validate_username(p['username']))
        names = [a.username for a in session.scalars(query)]
    found, errors = [], []
    for name in names:
        for item in wpcli.detect_installs({'username': name})['installs']:
            try:
                found.append(refresh_site({'username': name, 'domain': item['domain'], 'path': item['path']}))
            except Exception as exc:
                errors.append({'username': name, 'domain': item['domain'], 'path': item['path'], 'message': str(exc)})
    return {'found': len(found), 'sites': found, 'errors': errors}


def _forget(username, domain, path):
    account = cmdjobs._account(username)
    with write_session() as session:
        for record in session.scalars(select(WordPressInstall).where(WordPressInstall.account_id == account.id,
            WordPressInstall.domain == domain, WordPressInstall.path == path)).all():
            session.delete(record)
        _state(session, account.id, domain, path).hidden = True


def _removal_worker(username, root, config):
    pw = pwd.getpwnam(username)
    result = run(['runuser','-u',username,'--','env',f'HOME={pw.pw_dir}', '/usr/bin/python3',WORKER],
        input_text=json.dumps(config), timeout=300)
    if not result.ok: raise RuntimeError('Removal cleanup failed: '+result.stderr[-1000:])


def remove(p):
    with _operation_lock:
        username, domain, root = _site(p)
        ensure_idle(username)
        path = p.get('path', '').strip('/')
        if p.get('mode') == 'soft':
            _forget(username, domain, path)
            return {'status': 'removed', 'message': 'Panel record removed. Website files and database are unchanged.'}
        if p.get('mode') != 'hard': raise ValidationError('Choose soft or hard removal')
        expected = domain + ('/'+path if path else '')
        if p.get('confirmation') != expected:
            raise ValidationError('Type the exact installation address to confirm permanent removal')
        account = cmdjobs._account(username)
        database = _database_at(username, root)
        with write_session() as session:
            grant = session.scalar(select(DatabaseGrant).where(DatabaseGrant.account_id == account.id, DatabaseGrant.db_name == database))
            if grant is None: raise ValidationError('The database is not registered to this hosting account; permanent removal was refused')
            db_user = grant.db_user
            shared_user = session.scalar(select(DatabaseGrant).where(
                DatabaseGrant.db_user == db_user, DatabaseGrant.id != grant.id))
            if shared_user is not None:
                raise ValidationError('Another database shares this database user; permanent removal was refused')
            grant_id = grant.id
            domain_roots = [d.docroot for d in session.scalars(select(Domain).where(Domain.account_id == account.id))]
        for site in wpcli.detect_installs({'username': username})['installs']:
            other = wpcli._resolve_install_dir(username,site['domain'],site['path'])
            if os.path.realpath(other) != os.path.realpath(root) and _database_at(username, other) == database:
                raise ValidationError('Another WordPress installation shares this database. Remove its dependency before permanent deletion.')
        protected = []
        for other in domain_roots:
            rel = os.path.relpath(os.path.realpath(other), os.path.realpath(root))
            if rel != '.' and not rel.startswith('..'): protected.append(rel.split(os.sep)[0])
        config = {'root':root, 'action':'remove_prepare','php':settings.php_cli_bin,'phar':wpcli.ensure_wpcli(),
            'database_name':database, 'removal_id':secrets.token_hex(16),'protected':protected}
        removed = {'database':False}
        def rollback():
            if not removed['database']:
                _removal_worker(username,root,{**config,'action':'remove_rollback'})
        def finish():
            wordpress.handlers_database.mariadb.drop_database(database)
            removed['database'] = True
            wordpress.handlers_database.mariadb.drop_db_user(db_user)
            with write_session() as session:
                row = session.get(DatabaseGrant, grant_id)
                if row is not None: session.delete(row)
            _forget(username,domain,path)
            _removal_worker(username,root,{**config,'action':'remove_commit'})
        return cmdjobs.submit(username,'wpmanager',root,['/usr/bin/python3',WORKER],
            'Remove WordPress: '+expected,input_text=json.dumps(config),timeout=300,
            on_failure=rollback,on_success=finish)
