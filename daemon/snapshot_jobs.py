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
import tempfile
import time
import uuid

from sqlalchemy import select
from daemon import snapshot_storage as storage
from daemon.appcrypto import decrypt_env, encrypt_env
from daemon.procutil import run
from daemon.database_operations import serialized_worker
from shared.config import settings
from shared.db import write_session
from shared.models import Account, BackupJob, RestoreJob, DatabaseGrant, Domain, MailDomain, SnapshotDestination, SnapshotDestinationOperation, SnapshotPolicy, SnapshotRun, SnapshotRestore, utcnow
from shared.validation import ValidationError, validate_username

logger = logging.getLogger('borond.snapshot_jobs')
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='snapshots')
FREQUENCIES = {'manual': 0, 'hourly': 3600, 'daily': 86400, 'weekly': 604800, 'monthly': 2592000}
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
    s3_credentials = {}
    s3_file = credentials/'s3_credentials.enc'
    if destination.kind == 's3':
        if not s3_file.exists() or s3_file.is_symlink() or s3_file.stat().st_uid != os.geteuid() or s3_file.stat().st_mode & 0o077:
            raise ValidationError('S3 credentials are missing or have unsafe permissions')
        s3_credentials = decrypt_env(s3_file.read_text())
    return storage.Repository(kind=destination.kind, path=destination.path, namespace=destination.namespace,
        password_file=str(credentials/'password'), cache_dir=str(credentials/'cache'),
        ssh_host=connection.get('host',''), ssh_user=connection.get('user',''), ssh_port=connection.get('port',22),
        ssh_key_file=str(credentials/'ssh_key'), ssh_known_hosts_file=str(credentials/'known_hosts'),
        s3_endpoint=connection.get('endpoint',''), s3_bucket=connection.get('bucket',''),
        s3_region=connection.get('region',''), s3_credentials=s3_credentials)


def _destination_dict(row):
    credentials = private_directory('repositories',row.namespace)
    public = credentials/'ssh_key.pub'
    return {'id':row.id,'name':row.name,'kind':row.kind,'path':row.path,'connection':row.connection,
        'namespace':row.namespace,'status':row.status,'error':row.error,'enabled':row.enabled,
        'customer_visible':row.customer_visible,'capabilities':row.capabilities or {},
        'last_verified_at':row.last_verified_at.isoformat() if row.last_verified_at else None,
        'last_speed_bps':row.last_speed_bps,
        'ssh_public_key':public.read_text().strip() if public.exists() else None}


def destinations(params):
    with write_session() as session:
        rows = session.scalars(select(SnapshotDestination).order_by(SnapshotDestination.id)).all()
    return {'destinations':[_destination_dict(row) for row in rows]}


def create_destination(params):
    name = str(params.get('name','')).strip()
    if not 1 <= len(name) <= 100: raise ValidationError('Enter a destination name of up to 100 characters')
    namespace = uuid.uuid4().hex
    kind = params.get('kind','local')
    if kind == 's3':
        provider = str(params.get('s3_provider','custom')).strip().lower()
        if provider not in ('aws','cloudflare','backblaze','wasabi','digitalocean','minio','custom'):
            raise ValidationError('Choose a supported S3 provider')
        connection = {'provider':provider, 'endpoint':str(params.get('s3_endpoint','')).strip(),
            'bucket':str(params.get('s3_bucket','')).strip().lower(), 'region':str(params.get('s3_region','')).strip()}
        path = str(params.get('s3_prefix','')).strip().strip('/')
        s3_secrets = {key:str(params.get('s3_'+key,'')).strip() for key in ('access_key','secret_key','session_token')}
        if any(len(value) > 2048 or any(char in value for char in ('\0','\r','\n')) for value in s3_secrets.values()):
            raise ValidationError('Invalid S3 credential value')
    else:
        connection = {key:params.get('ssh_'+key,default) for key,default in [('host',''),('user',''),('port',22)]}
        path = params.get('path','')
    row = SnapshotDestination(name=name,kind=params.get('kind','local'),path=params.get('path',''),namespace=namespace,
        connection=connection,status='draft',enabled=True,customer_visible=True,
        capabilities={'incremental':True,'compressed':True,'archive':True})
    row.path = path
    host_key = params.get('ssh_host_key','').strip()
    if row.kind == 'ssh' and not re.fullmatch(r'(ssh-ed25519|ecdsa-sha2-nistp(?:256|384|521)|ssh-rsa) [A-Za-z0-9+/]+={0,3}(?: [^\r\n]*)?',host_key):
        raise ValidationError('Paste the SSH server public host key, obtained from a trusted source')
    with lock('configuration'):
        with write_session() as session:
            if session.scalar(select(SnapshotDestination).where(SnapshotDestination.name==name)):
                raise ValidationError('A destination with this name already exists')
        credentials = private_directory('repositories', namespace)
        try:
            _secret(credentials/'password', secrets.token_urlsafe(48)+'\n')
            # Local and SSH repositories can be validated before their
            # optional connection files exist. S3 validation must wait until
            # its encrypted write-only credentials have been persisted.
            spec = repository(row) if row.kind != 's3' else None
            if row.kind == 'ssh':
                result = run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',spec.ssh_key_file],timeout=30)
                result.raise_if_failed('Generate backup SSH key')
                host = connection['host'] if connection['port']==22 else f"[{connection['host']}]:{connection['port']}"
                _secret(credentials/'known_hosts', f'{host} {host_key}\n')
            elif row.kind == 's3':
                _secret(credentials/'s3_credentials.enc', encrypt_env(s3_secrets))
                # Validate the complete connection only after its write-only credentials exist.
                spec = repository(row)
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
            row = session.get(SnapshotDestination,ident);row.status='ready';row.error=None;row.last_verified_at=utcnow()
            return _destination_dict(row)


def recovery_key(params):
    row = _row(SnapshotDestination,params['id'])
    spec = repository(row)
    return {'password':storage._private_file(spec.password_file).read_text().strip(),
        'namespace':row.namespace,'path':row.path,'kind':row.kind}


def set_destination(params):
    ident=storage._positive(params['id'])
    allowed={key:params[key] for key in ('enabled','customer_visible') if key in params}
    if not allowed or any(not isinstance(value,bool) for value in allowed.values()):
        raise ValidationError('Choose a valid destination setting')
    with lock('configuration'),write_session() as session:
        row=session.get(SnapshotDestination,ident)
        if row is None:raise ValidationError('Backup destination not found')
        for key,value in allowed.items():setattr(row,key,value)
        session.flush()
        return _destination_dict(row)


def delete_destination(params):
    ident=storage._positive(params['id'])
    with lock('configuration'),write_session() as session:
        row=session.get(SnapshotDestination,ident)
        if row is None:raise ValidationError('Backup destination not found')
        policies=session.scalars(select(SnapshotPolicy).where(SnapshotPolicy.destination_id==ident)).all()
        policies += [policy for policy in session.scalars(select(SnapshotPolicy)).all()
                     if ident in (policy.options or {}).get('destination_ids',[]) and policy not in policies]
        if policies:raise ValidationError('Remove this destination from backup jobs before deleting it')
        if session.scalar(select(SnapshotRun.id).where(SnapshotRun.destination_id==ident,SnapshotRun.status.in_(ACTIVE))):
            raise ValidationError('Wait for active backup or restore work to finish')
        has_history=session.scalar(select(SnapshotRun.id).where(SnapshotRun.destination_id==ident)) is not None
        namespace=row.namespace
        if has_history:
            row.enabled=False;row.customer_visible=False;row.status='deleted';row.error='Destination configuration removed; remote backup data was not deleted'
            row.connection={};row.path='configuration-removed'
        else:
            operations=session.scalars(select(SnapshotDestinationOperation).where(SnapshotDestinationOperation.destination_id==ident)).all()
            for operation in operations:session.delete(operation)
            session.delete(row)
    credentials=private_directory('repositories')/namespace
    if credentials.exists() and not credentials.is_symlink():shutil.rmtree(credentials)
    return {'id':ident,'status':'configuration_removed','remote_data_deleted':False}


def _operation_dict(row):
    return {'id':row.id,'destination_id':row.destination_id,'action':row.action,'status':row.status,
        'progress_message':row.progress_message,'result':row.result,'error':row.error,
        'started_at':row.started_at.isoformat() if row.started_at else None,
        'completed_at':row.completed_at.isoformat() if row.completed_at else None}


def destination_operations(params):
    with write_session() as session:
        query=select(SnapshotDestinationOperation).order_by(SnapshotDestinationOperation.id.desc()).limit(100)
        if params.get('id'):query=query.where(SnapshotDestinationOperation.destination_id==storage._positive(params['id']))
        return {'operations':[_operation_dict(row) for row in session.scalars(query).all()]}


def queue_destination_operation(params):
    ident=storage._positive(params['id']);action=params.get('action')
    if action not in ('test','speed','reindex'):raise ValidationError('Choose test, speed, or reindex')
    destination=_row(SnapshotDestination,ident)
    if destination.status=='deleted':raise ValidationError('Destination configuration was removed')
    with write_session() as session:
        active=session.scalar(select(SnapshotDestinationOperation).where(
            SnapshotDestinationOperation.destination_id==ident,SnapshotDestinationOperation.status.in_(ACTIVE)))
        if active:raise ValidationError('A destination operation is already running')
        row=SnapshotDestinationOperation(destination_id=ident,action=action)
        session.add(row);session.flush();result=_operation_dict(row);operation_id=row.id
    _executor.submit(execute_destination_operation,operation_id)
    return result


def _update_operation(ident,**values):
    with write_session() as session:
        row=session.get(SnapshotDestinationOperation,ident)
        if row is None:return
        for key,value in values.items():setattr(row,key,value)


def _reindex_destination(destination,repo):
    snapshots=storage.all_snapshots(repo)
    prefix=f'boron:{destination.namespace}:account:'
    with write_session() as session:
        accounts={row.id:row for row in session.scalars(select(Account)).all()}
        policy=session.scalar(select(SnapshotPolicy).where(SnapshotPolicy.name==f'Recovered {destination.name}'))
        if policy is None:
            policy=SnapshotPolicy(name=f'Recovered {destination.name}'[:100],destination_id=destination.id,
                frequency='manual',enabled=False,options={'accounts':[],'excluded_accounts':[],
                'components':['files','databases','mail','config'],'include_paths':[],'exclude_patterns':[],
                'notification_channels':[],'retention_count':7,'mode':'incremental','destination_ids':[destination.id]})
            session.add(policy);session.flush()
        imported=0;unresolved=[]
        for item in snapshots:
            tag=next((str(tag) for tag in item.get('tags',[]) if str(tag).startswith(prefix)),None)
            try:account_id=int(tag.removeprefix(prefix)) if tag else 0
            except ValueError:account_id=0
            if account_id not in accounts:
                unresolved.append({'snapshot_id':item.get('id'),'account_id':account_id});continue
            if session.scalar(select(SnapshotRun.id).where(SnapshotRun.destination_id==destination.id,SnapshotRun.snapshot_id==item.get('id'))):continue
            when=item.get('time');started=utcnow()
            if isinstance(when,str):
                try:started=dt.datetime.fromisoformat(when.replace('Z','+00:00'))
                except ValueError:pass
            session.add(SnapshotRun(policy_id=policy.id,destination_id=destination.id,account_id=account_id,
                options=policy.options,status='completed',trigger='reindex',snapshot_id=item.get('id'),
                summary={'reindexed':True,'paths':item.get('paths',[])},progress_message='Recovered from destination index',
                started_at=started,completed_at=utcnow()))
            imported+=1
        return {'discovered':len(snapshots),'imported':imported,'unresolved':unresolved}


def execute_destination_operation(operation_id):
    operation=_row(SnapshotDestinationOperation,operation_id)
    try:
        with lock(f'repository-{operation.destination_id}'):
            destination=_row(SnapshotDestination,operation.destination_id);repo=repository(destination)
            _update_operation(operation_id,status='running',progress_message='Connecting to destination')
            started=time.monotonic()
            if operation.action=='test':
                storage.check(repo);result={'verified':True}
            elif operation.action=='reindex':
                storage.check(repo);_update_operation(operation_id,progress_message='Reading repository manifests')
                result=_reindex_destination(destination,repo)
            else:
                _update_operation(operation_id,progress_message='Running bounded upload test')
                probe=private_directory('probes',f'operation-{operation_id}')/'payload.bin'
                with probe.open('wb') as handle:handle.write(os.urandom(4*1024*1024))
                summary=storage.backup(repo,2147483647,[str(probe)],sandbox_roots=[str(probe.parent)])
                elapsed=max(.001,time.monotonic()-started)
                storage.forget(repo,2147483647,[summary['snapshot_id']],prune=True)
                shutil.rmtree(probe.parent)
                result={'bytes':4*1024*1024,'elapsed_seconds':round(elapsed,3),'bytes_per_second':int(4*1024*1024/elapsed)}
            with write_session() as session:
                row=session.get(SnapshotDestination,operation.destination_id)
                row.last_verified_at=utcnow();row.error=None
                if operation.action=='speed':row.last_speed_bps=result['bytes_per_second']
            _update_operation(operation_id,status='completed',progress_message='Completed',result=result,completed_at=utcnow())
    except Exception as exc:
        logger.exception('Destination operation %s failed',operation_id)
        with write_session() as session:
            row=session.get(SnapshotDestination,operation.destination_id)
            if row:row.error=str(exc)[-3000:]
        _update_operation(operation_id,status='failed',progress_message='Failed',error=str(exc)[-3000:],completed_at=utcnow())


def account_catalog(params):
    now=utcnow()
    with write_session() as session:
        accounts=session.scalars(select(Account).order_by(Account.username)).all()
        runs=session.scalars(select(SnapshotRun).order_by(SnapshotRun.id.desc())).all()
        archives=session.scalars(select(BackupJob).order_by(BackupJob.id.desc())).all()
        by_account={account.id:[] for account in accounts}
        for run in runs:by_account.setdefault(run.account_id,[]).append(('snapshot',run))
        for job in archives:by_account.setdefault(job.account_id,[]).append(('archive',job))
        result=[]
        for account in accounts:
            items=by_account.get(account.id,[])
            usable=[(kind,row) for kind,row in items if row.status=='completed']
            usable.sort(key=lambda item:item[1].completed_at or item[1].started_at,reverse=True)
            attempts=sorted(items,key=lambda item:item[1].started_at,reverse=True)
            latest=usable[0][1] if usable else None;attempt=attempts[0][1] if attempts else None
            age_hours=None
            if latest:
                stamp=latest.completed_at or latest.started_at
                if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=dt.timezone.utc)
                age_hours=round((now-stamp).total_seconds()/3600,1)
            result.append({'account_id':account.id,'username':account.username,'primary_domain':account.primary_domain,
                'account_status':account.status,'recovery_point_count':len(usable),
                'latest_recovery_at':(latest.completed_at or latest.started_at).isoformat() if latest else None,
                'latest_attempt_status':attempt.status if attempt else None,'age_hours':age_hours,
                'availability':'available' if usable else ('not_scheduled' if not attempts else 'no_backups')})
        return {'accounts':result,'generated_at':now.isoformat()}


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
    if not set(channels)<= {'email','telegram','webhook'}: raise ValidationError('Choose email or webhook notifications, or Telegram')
    retention = params.get('retention_count',7)
    if isinstance(retention,bool) or not isinstance(retention,int) or not 1<=retention<=365: raise ValidationError('Keep between 1 and 365 snapshots')
    mode = params.get('mode','incremental')
    if mode not in ('full','incremental','compressed','archive'): raise ValidationError('Choose full or incremental, compressed, or archive backup')
    destination_ids = params.get('destination_ids') or [params.get('destination_id')]
    if not isinstance(destination_ids,list) or not destination_ids or len(destination_ids)>8:
        raise ValidationError('Choose between one and eight destinations')
    destination_ids = list(dict.fromkeys(storage._positive(value) for value in destination_ids))
    return dict(accounts=accounts,excluded_accounts=excluded,components=components,include_paths=includes,
        exclude_patterns=excludes,notification_channels=channels,retention_count=retention,mode=mode,
        destination_ids=destination_ids,
        timezone=str(params.get('timezone') or 'UTC')[:64],
        retention_daily=max(0,min(365,int(params.get('retention_daily',retention)))),
        retention_weekly=max(0,min(104,int(params.get('retention_weekly',4)))),
        retention_monthly=max(0,min(120,int(params.get('retention_monthly',6)))))


def _policy_dict(row):
    return dict(id=row.id,name=row.name,destination_id=row.destination_id,frequency=row.frequency,
        enabled=row.enabled,options=row.options,last_queued_at=row.last_queued_at.isoformat() if row.last_queued_at else None)


def save_policy(params):
    name = str(params.get('name','')).strip()
    if not 1<=len(name)<=100: raise ValidationError('Enter a job name of up to 100 characters')
    frequency = params.get('frequency','manual')
    if frequency not in FREQUENCIES: raise ValidationError('Invalid backup frequency')
    if not isinstance(params.get('enabled',True),bool): raise ValidationError('Invalid enabled value')
    options = validate_options(params)
    destination_rows=[_row(SnapshotDestination,value) for value in options['destination_ids']]
    if any(row.status!='ready' or not row.enabled for row in destination_rows):
        raise ValidationError('Every destination must be ready and enabled before creating a job')
    destination = destination_rows[0]
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
        cancel_requested=row.cancel_requested,
        started_at=row.started_at.isoformat(),completed_at=row.completed_at.isoformat() if row.completed_at else None)


def runs(params):
    account = _account(params['username']) if params.get('username') else None
    with write_session() as session:
        query = select(SnapshotRun).order_by(SnapshotRun.id.desc()).limit(200)
        if account: query=query.where(SnapshotRun.account_id==account.id)
        rows=session.scalars(query).all()
        names=dict(session.execute(select(Account.id,Account.username).where(Account.id.in_([row.account_id for row in rows]))).all())
        return {'runs':[{**_run_dict(row,customer=account is not None),'username':names.get(row.account_id)} for row in rows]}


def cancel_run(params):
    ident=storage._positive(params['id'])
    with write_session() as session:
        row=session.get(SnapshotRun,ident)
        if row is None:raise ValidationError('Backup run not found')
        if row.status=='pending':
            row.status='cancelled';row.cancel_requested=True;row.progress_message='Cancelled before start';row.completed_at=utcnow()
        elif row.status=='running':
            row.cancel_requested=True;row.progress_message='Cancellation requested; the current safe storage step will finish'
        else:raise ValidationError('Only queued or running backups can be cancelled')
        session.flush();return _run_dict(row)


def retry_run(params):
    ident=storage._positive(params['id'])
    with lock('queue'),write_session() as session:
        source=session.get(SnapshotRun,ident)
        if source is None:raise ValidationError('Backup run not found')
        if source.status not in ('failed','cancelled'):raise ValidationError('Only failed or cancelled backups can be retried')
        if session.scalar(select(SnapshotRun.id).where(SnapshotRun.account_id==source.account_id,SnapshotRun.status.in_(ACTIVE))):
            raise ValidationError('This account already has active backup work')
        row=SnapshotRun(policy_id=source.policy_id,destination_id=source.destination_id,account_id=source.account_id,
            options=source.options,status='pending',trigger='retry')
        session.add(row);session.flush();result=_run_dict(row);new_id=row.id
    _executor.submit(execute_run,new_id)
    return result


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
        destination_ids=policy.options.get('destination_ids') or [policy.destination_id]
        destination_rows=[session.get(SnapshotDestination,value) for value in destination_ids]
        if any(row is None or row.status!='ready' or not row.enabled for row in destination_rows): raise ValidationError('Every backup destination must be ready and enabled')
        options=policy.options
        accounts=session.scalars(select(Account).where(Account.status=='active')).all()
        accounts=[a for a in accounts if (not options['accounts'] or a.username in options['accounts']) and a.username not in options['excluded_accounts']]
        skipped=[]
        for account in accounts:
            if any(session.scalar(select(model.id).where(model.account_id==account.id,model.status.in_(ACTIVE))) for model in (SnapshotRun,SnapshotRestore,BackupJob,RestoreJob)):
                skipped.append(account.username);continue
            for destination_id in destination_ids:
                row=SnapshotRun(policy_id=policy.id,destination_id=destination_id,account_id=account.id,
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
        mail_domains = _mail_sources(account, stage)
        manifest['mail_domains'] = [domain.domain for domain in mail_domains]
        (stage/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2))
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


def sandbox_roots(account, paths):
    home = Path(settings.home_base) / account.username
    roots = [str(home)]
    for value in paths:
        path = Path(value)
        if not path.is_relative_to(home):
            roots.append(str(path))
    return list(dict.fromkeys(roots))


@serialized_worker
def _mail_sources(account, stage):
    # Read registrations only after excluding mail provisioning/deletion; keep
    # the same lock through the provider capture and private metadata write.
    from daemon.snapshot_mail_metadata import capture
    from daemon.snapshot_db_metadata import write_metadata
    with write_session() as session:
        current = session.get(Account, account.id)
        if current is None or current.username != account.username or current.status != 'active':
            raise ValidationError('Account is no longer available for mail backup')
        domains = session.scalars(select(MailDomain).where(MailDomain.account_id == account.id)).all()
    write_metadata(stage/'mail-recovery.json', capture(account.username, domains))
    return domains


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
    account_id=row.account_id
    account=None
    should_notify=False
    try:
        with lock(f'account-{account_id}'),lock(f'repository-{row.destination_id}'):
            # A second worker must not execute the same persisted run again.
            row=_row(SnapshotRun,ident)
            if row.status!='pending':return
            if row.cancel_requested:
                _update(ident,status='cancelled',progress_message='Cancelled before start',completed_at=utcnow());return
            should_notify=True
            account=_row(Account,row.account_id)
            if account.status!='active':raise ValidationError('Account is no longer active')
            _update(ident,status='running',progress_message='Preparing account files and databases')
            paths=sources(account,row.options)
            if _row(SnapshotRun,ident).cancel_requested:
                _update(ident,status='cancelled',progress_message='Cancelled after preparation',completed_at=utcnow());return
            _update(ident,progress_message='Saving encrypted snapshot')
            repo=repository(_row(SnapshotDestination,row.destination_id))
            summary=storage.backup(repo,account.id,paths,policy_id=row.policy_id,
                excludes=row.options['exclude_patterns'],full_scan=row.options['mode']=='full',
                exclude_mail_staging='mail' in row.options['components'],
                sandbox_roots=sandbox_roots(account, paths))
            if _row(SnapshotRun,ident).cancel_requested:
                _update(ident,status='cancelled',snapshot_id=summary['snapshot_id'],summary=summary,
                    progress_message='Backup point completed; later steps cancelled',completed_at=utcnow());return
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
        if should_notify and account is not None:_notify(_row(SnapshotRun,ident),account)


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
