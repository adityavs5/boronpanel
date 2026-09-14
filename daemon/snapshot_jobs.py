"""Persistent incremental backup jobs. Privileged entry points are admin-only.

Customer reads always include the account username; storage paths and credentials
are resolved here, never accepted from customer requests.
"""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import uuid

from sqlalchemy import select
from daemon import snapshot_storage as storage
from daemon.procutil import run
from daemon.database_operations import serialized_worker
from shared.config import settings
from shared.db import write_session
from shared.models import Account, BackupJob, RestoreJob, DatabaseGrant, Domain, MailDomain, SnapshotDestination, SnapshotPolicy, SnapshotRun, SnapshotRestore, utcnow
from shared.validation import ValidationError, validate_username

logger = logging.getLogger('borond.snapshot_jobs')
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='snapshots')
FREQUENCIES = {'manual': 0, 'hourly': 3600, 'daily': 86400, 'weekly': 604800}
ACTIVE = ('pending', 'running')


def private_directory(*parts):
    base = Path(settings.snapshot_private_dir)
    if not base.is_absolute() or '..' in base.parts or base == Path('/'):
        raise ValidationError('Invalid private backup directory')
    # Refuse symlink ancestors: credentials and locks must stay in service-owned storage.
    for path in [*reversed(base.parents), base]:
        if path.is_symlink(): raise ValidationError('Private backup directory cannot use symbolic links')
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    if base.stat().st_uid != os.geteuid(): raise ValidationError('Private backup directory has an unexpected owner')
    base.chmod(0o700)
    target = base
    for part in parts:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', str(part)): raise ValidationError('Invalid private storage identifier')
        target = target / str(part)
        if target.is_symlink(): raise ValidationError('Private backup directory cannot use symbolic links')
        target.mkdir(exist_ok=True, mode=0o700)
        if target.stat().st_uid != os.geteuid(): raise ValidationError('Private backup directory has an unexpected owner')
        target.chmod(0o700)
    return target


@contextmanager
def lock(name, *, blocking=True):
    path = private_directory('locks') / name
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield
    finally:
        os.close(fd)


def _secret(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as handle: handle.write(content)


def _row(model, ident):
    with write_session() as session:
        row = session.get(model, storage._positive(ident))
        if row is None: raise ValidationError('Backup item not found')
        return row


def _account(username):
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == validate_username(username)))
        if account is None: raise ValidationError('Account not found')
        return account


def repository(destination):
    credentials = private_directory('repositories', destination.namespace)
    connection = destination.connection
    return storage.Repository(kind=destination.kind, path=destination.path, namespace=destination.namespace,
        password_file=str(credentials/'password'), cache_dir=str(credentials/'cache'),
        ssh_host=connection.get('host',''), ssh_user=connection.get('user',''), ssh_port=connection.get('port',22),
        ssh_key_file=str(credentials/'ssh_key'), ssh_known_hosts_file=str(credentials/'known_hosts'))


def _destination_dict(row):
    credentials = private_directory('repositories',row.namespace)
    public = credentials/'ssh_key.pub'
    return {'id':row.id,'name':row.name,'kind':row.kind,'path':row.path,'connection':row.connection,
        'namespace':row.namespace,'status':row.status,'error':row.error,
        'ssh_public_key':public.read_text().strip() if public.exists() else None}


def destinations(params):
    with write_session() as session:
        rows = session.scalars(select(SnapshotDestination).order_by(SnapshotDestination.id)).all()
    return {'destinations':[_destination_dict(row) for row in rows]}


def create_destination(params):
    name = str(params.get('name','')).strip()
    if not 1 <= len(name) <= 100: raise ValidationError('Enter a destination name of up to 100 characters')
    namespace = uuid.uuid4().hex
    connection = {key:params.get('ssh_'+key,default) for key,default in [('host',''),('user',''),('port',22)]}
    row = SnapshotDestination(name=name,kind=params.get('kind','local'),path=params.get('path',''),namespace=namespace,
        connection=connection,status='draft')
    # Validate connection and path syntax before persisting credentials.
    spec = repository(row)
    host_key = params.get('ssh_host_key','').strip()
    if row.kind == 'ssh' and not re.fullmatch(r'(ssh-ed25519|ecdsa-sha2-nistp(?:256|384|521)|ssh-rsa) [A-Za-z0-9+/]+={0,3}(?: [^\r\n]*)?',host_key):
        raise ValidationError('Paste the SSH server public host key, obtained from a trusted source')
    with lock('configuration'):
        with write_session() as session:
            if session.scalar(select(SnapshotDestination).where(SnapshotDestination.name==name)):
                raise ValidationError('A destination with this name already exists')
        credentials = Path(spec.password_file).parent
        try:
            _secret(credentials/'password', secrets.token_urlsafe(48)+'\n')
            if row.kind == 'ssh':
                result = run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',spec.ssh_key_file],timeout=30)
                result.raise_if_failed('Generate backup SSH key')
                host = connection['host'] if connection['port']==22 else f"[{connection['host']}]:{connection['port']}"
                _secret(credentials/'known_hosts', f'{host} {host_key}\n')
            with write_session() as session:
                session.add(row); session.flush()
                result = _destination_dict(row)
        except Exception:
            shutil.rmtree(credentials)
            raise
    return result


def initialize_destination(params):
    ident = storage._positive(params['id'])
    with lock(f'repository-{ident}'):
        row = _row(SnapshotDestination,ident)
        spec = repository(row)
        # Retry safely if init succeeded but a previous request disconnected.
        try:
            storage._execute(spec,['cat','config'],timeout=120)
        except storage.SnapshotStorageError:
            storage.initialize(spec)
        storage.check(spec)
        with write_session() as session:
            row = session.get(SnapshotDestination,ident);row.status='ready';row.error=None
            return _destination_dict(row)


def recovery_key(params):
    row = _row(SnapshotDestination,params['id'])
    spec = repository(row)
    return {'password':storage._private_file(spec.password_file).read_text().strip(),
        'namespace':row.namespace,'path':row.path,'kind':row.kind}


def _strings(value, name, limit=200):
    if not isinstance(value,list) or len(value)>limit or any(not isinstance(v,str) or not v or len(v)>1024 or any(c in v for c in '\0\r\n') for v in value):
        raise ValidationError(f'Invalid {name}')
    return list(dict.fromkeys(value))


def validate_options(params):
    accounts = _strings(params.get('accounts',[]),'account selection')
    excluded = _strings(params.get('excluded_accounts',[]),'excluded accounts')
    for username in accounts+excluded: _account(username)
    components = _strings(params.get('components',['files','databases','mail','config']),'components')
    if not components or not set(components)<= {'files','databases','mail','config'}: raise ValidationError('Choose backup components')
    includes = _strings(params.get('include_paths',[]),'included paths')
    for value in includes:
        if Path(value).is_absolute() or '..' in Path(value).parts: raise ValidationError('Included paths must be relative to the account home')
    excludes = _strings(params.get('exclude_patterns',[]),'exclusion patterns')
    channels = _strings(params.get('notification_channels',[]),'notification channels')
    if not set(channels)<= {'email','webhook'}: raise ValidationError('Choose email or webhook notifications')
    retention = params.get('retention_count',7)
    if isinstance(retention,bool) or not isinstance(retention,int) or not 1<=retention<=365: raise ValidationError('Keep between 1 and 365 snapshots')
    mode = params.get('mode','incremental')
    if mode not in ('full','incremental'): raise ValidationError('Choose full or incremental backup')
    return dict(accounts=accounts,excluded_accounts=excluded,components=components,include_paths=includes,
        exclude_patterns=excludes,notification_channels=channels,retention_count=retention,mode=mode)


def _policy_dict(row):
    return dict(id=row.id,name=row.name,destination_id=row.destination_id,frequency=row.frequency,
        enabled=row.enabled,options=row.options,last_queued_at=row.last_queued_at.isoformat() if row.last_queued_at else None)


def save_policy(params):
    name = str(params.get('name','')).strip()
    if not 1<=len(name)<=100: raise ValidationError('Enter a job name of up to 100 characters')
    frequency = params.get('frequency','manual')
    if frequency not in FREQUENCIES: raise ValidationError('Invalid backup frequency')
    if not isinstance(params.get('enabled',True),bool): raise ValidationError('Invalid enabled value')
    destination = _row(SnapshotDestination,params['destination_id'])
    if destination.status!='ready': raise ValidationError('Initialize the destination before creating a job')
    options = validate_options(params)
    with lock('configuration'), write_session() as session:
        row = session.get(SnapshotPolicy,params['id']) if params.get('id') else SnapshotPolicy()
        if row is None: raise ValidationError('Backup job not found')
        duplicate = session.scalar(select(SnapshotPolicy).where(SnapshotPolicy.name==name))
        if duplicate and duplicate.id!=row.id: raise ValidationError('A job with this name already exists')
        row.name=name;row.destination_id=destination.id;row.options=options;row.frequency=frequency;row.enabled=params.get('enabled',True)
        session.add(row);session.flush()
        return _policy_dict(row)


def policies(params):
    with write_session() as session:
        return {'policies':[_policy_dict(row) for row in session.scalars(select(SnapshotPolicy).order_by(SnapshotPolicy.id)).all()]}


def _run_dict(row, customer=False):
    options = {k:v for k,v in row.options.items() if k in ('components','include_paths','exclude_patterns','mode')} if customer else row.options
    return dict(id=row.id,policy_id=row.policy_id,destination_id=row.destination_id,account_id=row.account_id,
        status=row.status,trigger=row.trigger,snapshot_id=row.snapshot_id,summary=row.summary,options=options,
        notification_results=row.notification_results,progress_message=row.progress_message,error=row.error,
        started_at=row.started_at.isoformat(),completed_at=row.completed_at.isoformat() if row.completed_at else None)


def runs(params):
    account = _account(params['username']) if params.get('username') else None
    with write_session() as session:
        query = select(SnapshotRun).order_by(SnapshotRun.id.desc()).limit(200)
        if account: query=query.where(SnapshotRun.account_id==account.id)
        rows=session.scalars(query).all()
        names=dict(session.execute(select(Account.id,Account.username).where(Account.id.in_([row.account_id for row in rows]))).all())
        return {'runs':[{**_run_dict(row,customer=account is not None),'username':names.get(row.account_id)} for row in rows]}


def queue_policy(params):
    ids=[]
    with lock('queue'), write_session() as session:
        policy=session.get(SnapshotPolicy,storage._positive(params['id']))
        if not policy: raise ValidationError('Backup job not found')
        scheduled=params.get('trigger')=='scheduled'
        if scheduled:
            interval=FREQUENCIES[policy.frequency]
            last=policy.last_queued_at
            if last and last.tzinfo is None:last=last.replace(tzinfo=dt.timezone.utc)
            if not policy.enabled or not interval or (last and (utcnow()-last).total_seconds()<interval):return {'run_ids':[]}
        if session.get(SnapshotDestination,policy.destination_id).status!='ready': raise ValidationError('Backup destination is not ready')
        options=policy.options
        accounts=session.scalars(select(Account).where(Account.status=='active')).all()
        accounts=[a for a in accounts if (not options['accounts'] or a.username in options['accounts']) and a.username not in options['excluded_accounts']]
        skipped=[]
        for account in accounts:
            if any(session.scalar(select(model.id).where(model.account_id==account.id,model.status.in_(ACTIVE))) for model in (SnapshotRun,SnapshotRestore,BackupJob,RestoreJob)):
                skipped.append(account.username);continue
            row=SnapshotRun(policy_id=policy.id,destination_id=policy.destination_id,account_id=account.id,
                options=options,status='pending',trigger='scheduled' if scheduled else 'manual')
            session.add(row);session.flush();ids.append(row.id)
        if ids:policy.last_queued_at=utcnow()
    for ident in ids:_executor.submit(execute_run,ident)
    return {'run_ids':ids,'skipped_busy_accounts':skipped}


def _update(ident, **values):
    with write_session() as session:
        row=session.get(SnapshotRun,ident)
        for key,value in values.items():setattr(row,key,value)


def sources(account, options):
    """Return account-owned raw paths and stable private metadata/SQL paths."""
    home=Path(settings.home_base)/account.username
    if home.is_symlink() or home.resolve()!=home.absolute() or not home.is_dir():raise ValidationError('Account home is not a regular directory')
    paths=[]
    if 'files' in options['components']:
        for value in options['include_paths'] or ['.']:
            selected=home/value
            if not selected.exists() or not selected.resolve().is_relative_to(home):raise ValidationError('Selected backup path is missing or outside the account home')
            paths.append(str(selected))
    stage=private_directory('sources',f'account-{account.id}')
    # Reuse the staging path between snapshots, but never retain removed database dumps.
    for item in stage.iterdir():
        if item.is_dir() and not item.is_symlink():shutil.rmtree(item)
        else:item.unlink()
    with write_session() as session:
        databases=session.scalars(select(DatabaseGrant).where(DatabaseGrant.account_id==account.id)).all()
        domains=session.scalars(select(Domain).where(Domain.account_id==account.id)).all()
        mail_domains=session.scalars(select(MailDomain).where(MailDomain.account_id==account.id)).all()
    manifest={'format':1,'account_id':account.id,'username':account.username,'php_version':account.php_version,
        'components':options['components'],'home':str(home),'domains':[{'domain':d.domain,'docroot':d.docroot,'kind':d.kind,'php_version':d.php_version} for d in domains],
        'databases':[{'name':d.db_name,'user':d.db_user} for d in databases],'mail_domains':[d.domain for d in mail_domains]}
    (stage/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2))
    if 'databases' in options['components']:
        current_databases=_database_sources(account, stage)
        manifest['databases']=[{'name':db.db_name,'user':db.db_user} for db in current_databases]
        (stage/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2))
    if 'mail' in options['components']:
        from daemon.snapshot_mail_metadata import capture as capture_mail
        from daemon.snapshot_db_metadata import write_metadata
        write_metadata(stage/'mail-recovery.json', capture_mail(account.username, mail_domains))
        for domain in mail_domains:
            root=Path(settings.mail_base)/domain.domain
            if root.exists():
                if root.is_symlink() or root.resolve()!=root.absolute():raise ValidationError('Mail storage contains an unsafe path')
                paths.append(str(root))
    if 'config' in options['components']:
        from daemon import cron
        from daemon.snapshot_php import capture as capture_php
        manifest['php_configuration']=capture_php(account)
        from daemon.snapshot_dns import capture as capture_dns, legacy_zones
        manifest['dns_configuration']=capture_dns(account)
        manifest['cron_configuration']=cron.capture_configuration(account.username)
        manifest['cron_jobs']=cron.parse_jobs(manifest['cron_configuration']['lines'])
        manifest['dns_zones']=legacy_zones(manifest['dns_configuration'])
        (stage/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2))
    return paths+[str(stage)]


@serialized_worker
def _database_sources(account, stage):
    # Keep credentials and SQL export together while panel mutations are excluded.
    from daemon.snapshot_databases import dump_database
    from daemon.snapshot_db_metadata import capture, write_metadata
    with write_session() as session:
        databases=session.scalars(select(DatabaseGrant).where(DatabaseGrant.account_id==account.id)).all()
    write_metadata(stage/'database-recovery.json', capture(account.username, databases))
    database_dir=stage/'databases';database_dir.mkdir(mode=0o700)
    for db in databases:
        dump_database(db.db_name,database_dir/f'{db.db_name}.sql',stage)
    return databases


def _notify(row,account):
    from daemon import notifications,webhooks
    result={}
    event='backup.completed' if row.status=='completed' else 'backup.failed'
    for channel in row.options['notification_channels']:
        try:
            response=(notifications.maybe_send if channel=='email' else webhooks.maybe_trigger)(event,account,job_id=row.id,error=row.error)
            result[channel]='dispatched' if response else 'not dispatched (check channel settings and account preferences)'
        except Exception:
            logger.exception('Snapshot notification failed');result[channel]='failed'
    _update(row.id,notification_results=result)


def execute_run(ident):
    row=_row(SnapshotRun,ident)
    account=_row(Account,row.account_id)
    should_notify=False
    try:
        with lock(f'account-{account.id}'),lock(f'repository-{row.destination_id}'):
            # A second worker must not execute the same persisted run again.
            row=_row(SnapshotRun,ident)
            if row.status!='pending':return
            should_notify=True
            if account.status!='active':raise ValidationError('Account is no longer active')
            _update(ident,status='running',progress_message='Preparing account files and databases')
            paths=sources(account,row.options)
            _update(ident,progress_message='Saving encrypted snapshot')
            repo=repository(_row(SnapshotDestination,row.destination_id))
            summary=storage.backup(repo,account.id,paths,policy_id=row.policy_id,
                excludes=row.options['exclude_patterns'],full_scan=row.options['mode']=='full',
                exclude_mail_staging='mail' in row.options['components'])
            _update(ident,snapshot_id=summary['snapshot_id'],summary=summary,progress_message='Applying retention')
            items=[s for s in storage.snapshots(repo,account.id) if f'policy:{row.policy_id}' in s.get('tags',[])]
            items.sort(key=lambda s:s['time'],reverse=True)
            obsolete=[s['id'] for s in items[row.options['retention_count']:]]
            if obsolete:
                storage.forget(repo,account.id,obsolete,prune=True)
                with write_session() as session:
                    expired=session.scalars(select(SnapshotRun).where(SnapshotRun.account_id==account.id,
                        SnapshotRun.destination_id==row.destination_id,SnapshotRun.snapshot_id.in_(obsolete))).all()
                    for old in expired:old.status='expired';old.progress_message='Removed by retention policy'
            from daemon.snapshot_restores import apply_safety_retention
            apply_safety_retention(repo, account.id, row.destination_id, row.policy_id,
                                   row.options['retention_count'])
            _update(ident,status='completed',progress_message='Snapshot ready',completed_at=utcnow())
    except Exception as exc:
        logger.exception('Snapshot run %s failed',ident)
        _update(ident,status='failed',error=str(exc)[-3000:],progress_message='Backup failed',completed_at=utcnow())
    finally:
        if should_notify:_notify(_row(SnapshotRun,ident),account)


def _restore_entries(entries,account):
    home=Path(settings.home_base)/account.username
    return [{**row,'restore_path':str(Path(row['path']).relative_to(home)) if Path(row['path']).is_relative_to(home) and not Path(row['path']).is_relative_to(home/'.php') else None} for row in entries]


def browse(params):
    account=_account(params['username'])
    row=_row(SnapshotRun,params['run_id'])
    if row.account_id!=account.id or not row.snapshot_id:raise ValidationError('Snapshot not found for this account')
    try:
        with lock(f'repository-{row.destination_id}',blocking=False):
            repo=repository(_row(SnapshotDestination,row.destination_id))
            directory=params.get('directory','/')
            if directory=='/':
                snapshot=storage.owned_snapshot(repo,account.id,row.snapshot_id)
                roots=[]
                home=Path(settings.home_base)/account.username
                for source in snapshot.get('paths',[]):
                    path=Path(source)
                    nodes=storage.entries(repo,account.id,row.snapshot_id,str(path.parent))
                    node=next((n for n in nodes if n.get('path')==str(path)),None)
                    if node is None:continue
                    if path==home:label='Account files'
                    elif path.is_relative_to(home):label=str(path.relative_to(home))
                    elif path.is_relative_to(Path(settings.mail_base)):label=f'Email: {path.name}'
                    else:label='Database exports & configuration'
                    roots.append({**node,'name':label})
                return {'entries':_restore_entries(roots,account)}
            return {'entries':_restore_entries(storage.entries(repo,account.id,row.snapshot_id,directory),account)}
    except BlockingIOError:
        raise ValidationError('This destination is busy with a backup or retention task. Try again shortly.') from None


def run_scheduled():
    with write_session() as session:
        ids=list(session.scalars(select(SnapshotPolicy.id).where(SnapshotPolicy.enabled==True)))
    count=0
    for ident in ids:
        try:count+=len(queue_policy({'id':ident,'trigger':'scheduled'})['run_ids'])
        except Exception:logger.exception('Could not queue scheduled snapshot policy %s',ident)
    return count


def recover_runs():
    """Startup recovery; locks distinguish live scheduled workers from dead ones."""
    with write_session() as session:
        rows=session.scalars(select(SnapshotRun).where(SnapshotRun.status.in_(ACTIVE))).all()
    for row in rows:
        if row.status=='pending':
            _executor.submit(execute_run,row.id)
            continue
        try:
            with lock(f'account-{row.account_id}',blocking=False):
                current=_row(SnapshotRun,row.id)
                if current.status=='running':
                    _update(row.id,status='failed',error='Backup worker was interrupted; run the job again.',
                        progress_message='Interrupted',completed_at=utcnow())
        except BlockingIOError:
            continue
