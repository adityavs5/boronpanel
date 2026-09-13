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
from shared.models import Account, Domain, WordPressInstall, WordPressJob, CommandRun
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
        records = {(r.domain, r.path): (r.admin_user, r.installed_at.isoformat()) for r in s.scalars(select(WordPressInstall)).all()}
        account_ids = [a[0] for a in accounts]
        recent_installs = s.scalars(select(WordPressJob).where(WordPressJob.account_id.in_(account_ids)).order_by(WordPressJob.id.desc()).limit(10)).all() if account_ids else []
        recent_commands = s.scalars(select(CommandRun).where(CommandRun.account_id.in_(account_ids), CommandRun.kind.in_(['wpmanager','wpcli'])).order_by(CommandRun.id.desc()).limit(10)).all() if account_ids else []
        activity = [{'id': 'install-'+str(j.id), 'label': 'Install WordPress: '+j.domain, 'status': j.status, 'message': j.progress_message, 'error': j.error} for j in recent_installs]
        activity += [{'id': 'manage-'+str(j.id), 'label': j.command_display, 'status': j.status, 'error': j.error} for j in recent_commands]
    installs, errors = [], []
    for _, name in accounts:
        try:
            for item in wpcli.detect_installs({'username': name})['installs']:
                admin_user, installed_at = records.get((item['domain'],item['path']), ('',None))
                item.update(username=name, account_status=statuses[name], admin_user=admin_user, installed_at=installed_at,
                            url='https://' + item['domain'] + ('/' + item['path'] if item['path'] else ''))
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
    return {'url': 'https://' + domain + '/' + (path+'/' if path else '') + filename, 'token': token, 'expires_in': 90}


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
