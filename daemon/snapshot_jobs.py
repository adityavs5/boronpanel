"""Persistent incremental backup jobs. Privileged entry points are admin-only.

Customer reads always include the account username; storage paths and credentials
are resolved here, never accepted from customer requests.
"""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import configparser
import datetime as dt
import fcntl
import fnmatch
import hashlib
import json
import logging
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import tempfile
import tarfile
import time
import uuid
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import select
from daemon import snapshot_storage as storage
from daemon.appcrypto import decrypt_env, decrypt_secret, encrypt_env, encrypt_secret
from daemon.procutil import run
from daemon.database_operations import serialized_worker
from shared.config import settings
from shared.db import write_session
from shared.models import Account, AppInstall, BackupJob, RestoreJob, DatabaseGrant, Domain, FtpAccount, GitRepo, MailDomain, NodeApp, PythonApp, RedisInstance, SnapshotDestination, SnapshotDestinationOperation, SnapshotDownload, SnapshotOAuthState, SnapshotPolicy, SnapshotRun, SnapshotRestore, WordPressInstall, utcnow
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


def download_directory(*parts):
    """Private download staging traversable only by the API service group."""
    base=Path(settings.snapshot_download_dir)
    if not base.is_absolute() or '..' in base.parts or base==Path('/'):
        raise ValidationError('Invalid backup download directory')
    for path in [*reversed(base.parents),base]:
        if path.is_symlink():raise ValidationError('Backup download directory cannot use symbolic links')
    base.mkdir(parents=True,exist_ok=True,mode=0o710)
    try:api=pwd.getpwnam('boron-api')
    except KeyError:api=None
    if base.stat().st_uid!=os.geteuid():raise ValidationError('Backup download directory has an unexpected owner')
    if api:os.chown(base,os.geteuid(),api.pw_gid)
    base.chmod(0o710)
    target=base
    for part in parts:
        if not re.fullmatch(r'[A-Za-z0-9_-]+',str(part)):raise ValidationError('Invalid download storage identifier')
        target=target/str(part);target.mkdir(exist_ok=True,mode=0o700)
        if target.is_symlink() or target.stat().st_uid!=os.geteuid():raise ValidationError('Unsafe backup download directory')
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
    transport_credentials = {}
    s3_file = credentials/'s3_credentials.enc'
    if destination.kind == 's3':
        if not s3_file.exists() or s3_file.is_symlink() or s3_file.stat().st_uid != os.geteuid() or s3_file.stat().st_mode & 0o077:
            raise ValidationError('S3 credentials are missing or have unsafe permissions')
        s3_credentials = decrypt_env(s3_file.read_text())
    transport_file = credentials/'transport_credentials.enc'
    if destination.kind in ('ssh','sftp') and transport_file.exists():
        if not transport_file.exists() or transport_file.is_symlink() or transport_file.stat().st_mode & 0o077:
            raise ValidationError('SSH credentials are missing or have unsafe permissions')
        transport_credentials = decrypt_env(transport_file.read_text())
    return storage.Repository(kind=destination.kind, path=destination.path, namespace=destination.namespace,
        password_file=str(credentials/'password'), cache_dir=str(credentials/'cache'),
        ssh_host=connection.get('host',''), ssh_user=connection.get('user',''), ssh_port=connection.get('port',22),
        ssh_key_file=str(credentials/'ssh_key'), ssh_known_hosts_file=str(credentials/'known_hosts'),
        ssh_auth=connection.get('auth','key'),ssh_password=transport_credentials.get('password',''),
        ssh_askpass_file=str(credentials/'ssh_askpass'),
        s3_endpoint=connection.get('endpoint',''), s3_bucket=connection.get('bucket',''),
        s3_region=connection.get('region',''), s3_credentials=s3_credentials,
        rclone_config_file=str(credentials/'rclone.conf'),rclone_remote_name='boron_drive')


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
    elif kind in ('ssh','sftp'):
        auth=str(params.get('ssh_auth','key')).strip().lower()
        if auth not in ('key','password'):raise ValidationError('Choose SSH key or password authentication')
        connection = {key:params.get('ssh_'+key,default) for key,default in [('host',''),('user',''),('port',22)]}
        connection['auth']=auth
        path = params.get('path','')
        password=str(params.get('ssh_password',''))
        passphrase=str(params.get('ssh_key_passphrase',''))
        supplied_key=str(params.get('ssh_private_key',''))
        if auth=='password' and (not password or len(password)>2048 or any(c in password for c in ('\0','\r','\n'))):
            raise ValidationError('Enter a valid SSH password')
        if auth=='key' and supplied_key and (len(supplied_key)>16384 or '\0' in supplied_key):
            raise ValidationError('Invalid SSH private key')
        if passphrase and (len(passphrase)>2048 or any(c in passphrase for c in ('\0','\r','\n'))):
            raise ValidationError('Invalid SSH key passphrase')
        if passphrase and not supplied_key:raise ValidationError('A passphrase is only used with a supplied encrypted private key')
    elif kind == 'drive':
        path=str(params.get('drive_folder','')).strip().strip('/')
        client_id=str(params.get('drive_client_id','')).strip()
        client_secret=str(params.get('drive_client_secret','')).strip()
        if not client_id or not client_secret or max(map(len,(client_id,client_secret)))>4096 or any(c in client_id+client_secret for c in '\0\r\n'):
            raise ValidationError('Google Drive OAuth client ID and secret are required')
        connection={'provider':'google_drive','oauth_connected':False}
    elif kind == 'local':
        connection={};path=params.get('path','')
    else:
        raise ValidationError('Choose local, SSH, SFTP, Amazon S3, Backblaze B2, S3-compatible, or Google Drive storage')
    capabilities={'incremental':kind!='drive','full':kind!='drive','compressed':True,'archive':True}
    row = SnapshotDestination(name=name,kind=kind,path=path,namespace=namespace,
        connection=connection,status='draft',enabled=True,customer_visible=True,
        capabilities=capabilities)
    row.path = path
    host_key = params.get('ssh_host_key','').strip()
    if row.kind in ('ssh','sftp') and not re.fullmatch(r'(ssh-ed25519|ecdsa-sha2-nistp(?:256|384|521)|ssh-rsa) [A-Za-z0-9+/]+={0,3}(?: [^\r\n]*)?',host_key):
        raise ValidationError('Paste the SSH server public host key, obtained from a trusted source')
    with lock('configuration'):
        with write_session() as session:
            if session.scalar(select(SnapshotDestination).where(SnapshotDestination.name==name)):
                raise ValidationError('A destination with this name already exists')
        credentials = private_directory('repositories', namespace)
        try:
            _secret(credentials/'password', secrets.token_urlsafe(48)+'\n')
            # Remote transports are validated only after their private
            # credential files have been persisted.
            spec = repository(row) if row.kind == 'local' else None
            if row.kind in ('ssh','sftp'):
                if connection['auth']=='key':
                    if supplied_key:
                        _secret(credentials/'ssh_key',supplied_key.rstrip('\n')+'\n')
                        key_env=None
                        if passphrase:
                            _secret(credentials/'transport_credentials.enc',encrypt_env({'password':passphrase}))
                            _secret(credentials/'ssh_askpass','#!/bin/sh\nprintf "%s\\n" "$BORON_SSH_PASSWORD"\n')
                            (credentials/'ssh_askpass').chmod(0o700)
                            key_env={**os.environ,'BORON_SSH_PASSWORD':passphrase,'SSH_ASKPASS':str(credentials/'ssh_askpass'),
                                'SSH_ASKPASS_REQUIRE':'force','DISPLAY':'boron:0'}
                        result=run(['/usr/bin/ssh-keygen','-y','-f',str(credentials/'ssh_key')],timeout=30,env=key_env)
                        result.raise_if_failed('Validate backup SSH key')
                        _secret(credentials/'ssh_key.pub',result.stdout.strip()+'\n')
                    else:
                        result = run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(credentials/'ssh_key')],timeout=30)
                        result.raise_if_failed('Generate backup SSH key')
                else:
                    _secret(credentials/'transport_credentials.enc',encrypt_env({'password':password}))
                    _secret(credentials/'ssh_askpass','#!/bin/sh\nprintf "%s\\n" "$BORON_SSH_PASSWORD"\n')
                    (credentials/'ssh_askpass').chmod(0o700)
                host = connection['host'] if connection['port']==22 else f"[{connection['host']}]:{connection['port']}"
                _secret(credentials/'known_hosts', f'{host} {host_key}\n')
                spec=repository(row)
            elif row.kind == 's3':
                _secret(credentials/'s3_credentials.enc', encrypt_env(s3_secrets))
                # Validate the complete connection only after its write-only credentials exist.
                spec = repository(row)
            elif row.kind == 'drive':
                _secret(credentials/'drive_client.enc',encrypt_env({'client_id':client_id,'client_secret':client_secret}))
            with write_session() as session:
                session.add(row); session.flush()
                result = _destination_dict(row)
        except Exception:
            shutil.rmtree(credentials)
            raise
    return result


def _drive_credentials(destination):
    if destination.kind!='drive':raise ValidationError('This destination is not Google Drive')
    path=private_directory('repositories',destination.namespace)/'drive_client.enc'
    if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValidationError('Google Drive OAuth client configuration is missing')
    return decrypt_env(path.read_text())


def drive_oauth_start(params):
    destination=_row(SnapshotDestination,params['id']);client=_drive_credentials(destination)
    redirect_uri=str(params.get('redirect_uri',''))
    parsed=urlsplit(redirect_uri)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValidationError('Google Drive callback must use this panel over HTTPS')
    if not parsed.path.endswith(f'/api/v1/backups/snapshots/destinations/{destination.id}/drive/oauth/callback'):
        raise ValidationError('Invalid Google Drive callback URL')
    state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(64)
    challenge=__import__('base64').urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    with write_session() as session:
        for old in session.scalars(select(SnapshotOAuthState).where(
                SnapshotOAuthState.destination_id==destination.id)).all():session.delete(old)
        session.add(SnapshotOAuthState(destination_id=destination.id,state_hash=hashlib.sha256(state.encode()).hexdigest(),
            verifier_enc=encrypt_secret(verifier),redirect_uri=redirect_uri,expires_at=utcnow()+dt.timedelta(minutes=10)))
    query=urlencode({'client_id':client['client_id'],'redirect_uri':redirect_uri,'response_type':'code',
        'scope':'https://www.googleapis.com/auth/drive.file','access_type':'offline','prompt':'consent',
        'state':state,'code_challenge':challenge,'code_challenge_method':'S256'})
    return {'authorization_url':'https://accounts.google.com/o/oauth2/v2/auth?'+query,'expires_in':600}


def _write_drive_config(destination,client,token):
    credentials=private_directory('repositories',destination.namespace);config=credentials/'rclone.conf'
    parser=configparser.RawConfigParser();parser.add_section('boron_drive')
    parser.set('boron_drive','type','drive');parser.set('boron_drive','scope','drive.file')
    parser.set('boron_drive','client_id',client['client_id']);parser.set('boron_drive','client_secret',client['client_secret'])
    parser.set('boron_drive','token',json.dumps(token,separators=(',',':')))
    temporary=config.with_suffix('.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as handle:parser.write(handle)
    os.replace(temporary,config);config.chmod(0o600)


def drive_oauth_callback(params):
    raw_state=str(params.get('state',''));code=str(params.get('code',''))
    if not raw_state or not code or len(raw_state)>256 or len(code)>4096:raise ValidationError('Google Drive authorization response is incomplete')
    digest=hashlib.sha256(raw_state.encode()).hexdigest();now=utcnow()
    expired=False
    with write_session() as session:
        state=session.scalar(select(SnapshotOAuthState).where(SnapshotOAuthState.state_hash==digest))
        if state is None:raise ValidationError('Google Drive authorization state is invalid or already used')
        expires=state.expires_at if state.expires_at.tzinfo else state.expires_at.replace(tzinfo=dt.timezone.utc)
        expired=expires<=now
        destination_id=state.destination_id;redirect_uri=state.redirect_uri;verifier='' if expired else decrypt_secret(state.verifier_enc)
        if destination_id!=storage._positive(params['id']):raise ValidationError('Google Drive authorization state does not match this destination')
        session.delete(state)
    if expired:raise ValidationError('Google Drive authorization expired; connect again')
    destination=_row(SnapshotDestination,destination_id);client=_drive_credentials(destination)
    try:
        response=httpx.post('https://oauth2.googleapis.com/token',data={'client_id':client['client_id'],
            'client_secret':client['client_secret'],'code':code,'code_verifier':verifier,
            'grant_type':'authorization_code','redirect_uri':redirect_uri},timeout=30)
        response.raise_for_status();payload=response.json()
    except Exception as exc:raise ValidationError('Google Drive rejected the authorization code') from exc
    if not payload.get('access_token') or not payload.get('refresh_token'):
        raise ValidationError('Google Drive did not provide a reusable refresh token; reconnect and approve access')
    expiry=now+dt.timedelta(seconds=max(60,int(payload.get('expires_in',3600))))
    token={'access_token':payload['access_token'],'token_type':payload.get('token_type','Bearer'),
        'refresh_token':payload['refresh_token'],'expiry':expiry.isoformat().replace('+00:00','Z')}
    _write_drive_config(destination,client,token)
    with write_session() as session:
        row=session.get(SnapshotDestination,destination.id);row.connection={**row.connection,'oauth_connected':True};row.error=None;session.flush()
        return _destination_dict(row)


def drive_folders(params):
    destination=_row(SnapshotDestination,params['id'])
    if not destination.connection.get('oauth_connected'):raise ValidationError('Connect Google Drive first')
    config=private_directory('repositories',destination.namespace)/'rclone.conf'
    result=run([settings.rclone_bin,'lsjson','--config',str(config),'--dirs-only','--max-depth','1','boron_drive:'],timeout=120)
    result.raise_if_failed('List Google Drive folders')
    try:items=json.loads(result.stdout)
    except ValueError as exc:raise ValidationError('Google Drive returned an invalid folder listing') from exc
    return {'folders':[{'name':item.get('Name',''),'path':item.get('Path',''),'id':item.get('ID','')}
        for item in items if item.get('IsDir') and isinstance(item.get('Path'),str)]}


def set_drive_folder(params):
    destination=_row(SnapshotDestination,params['id'])
    if destination.kind!='drive' or not destination.connection.get('oauth_connected'):raise ValidationError('Connect Google Drive first')
    path=str(params.get('path','')).strip().strip('/')
    if not path or len(path)>512 or path.startswith('/') or '..' in Path(path).parts or not re.fullmatch(r'[A-Za-z0-9._ /-]+',path):
        raise ValidationError('Choose a valid Google Drive folder')
    with write_session() as session:
        row=session.get(SnapshotDestination,destination.id);row.path=path;row.status='draft';session.flush();return _destination_dict(row)


def revoke_drive(params):
    destination=_row(SnapshotDestination,params['id']);credentials=private_directory('repositories',destination.namespace)
    config=credentials/'rclone.conf'
    if config.is_file():
        parser=configparser.RawConfigParser();parser.read(config);token={}
        try:token=json.loads(parser.get('boron_drive','token'))
        except (ValueError,configparser.Error):pass
        value=token.get('refresh_token') or token.get('access_token')
        if value:
            try:httpx.post('https://oauth2.googleapis.com/revoke',data={'token':value},timeout=20)
            except Exception:logger.warning('Google Drive token revocation request failed',exc_info=True)
        config.unlink()
    with write_session() as session:
        row=session.get(SnapshotDestination,destination.id);row.connection={**row.connection,'oauth_connected':False};row.status='draft';row.error=None;session.flush();return _destination_dict(row)


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
        if session.scalar(select(SnapshotRestore.id).join(SnapshotRun,SnapshotRun.id==SnapshotRestore.run_id).where(
                SnapshotRun.destination_id==ident,SnapshotRestore.status.in_(ACTIVE))):
            raise ValidationError('Wait for active backup or restore work to finish')
        if session.scalar(select(SnapshotDownload.id).join(SnapshotRun,SnapshotRun.id==SnapshotDownload.run_id).where(
                SnapshotRun.destination_id==ident,SnapshotDownload.status.in_(ACTIVE))):
            raise ValidationError('Wait for active backup or download work to finish')
        if session.scalar(select(SnapshotDestinationOperation.id).where(
                SnapshotDestinationOperation.destination_id==ident,SnapshotDestinationOperation.status.in_(ACTIVE))):
            raise ValidationError('Wait for the destination operation to finish')
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


def destination_inventory(params):
    destination=_row(SnapshotDestination,params['id'])
    if destination.status=='deleted':raise ValidationError('Destination configuration was removed')
    try:
        with lock(f'repository-{destination.id}',blocking=False):items=storage.all_snapshots(repository(destination))
    except BlockingIOError:raise ValidationError('This destination is busy. Try again shortly.') from None
    prefix=f'boron:{destination.namespace}:account:'
    account_ids=[]
    for item in items:
        tag=next((str(tag) for tag in item.get('tags',[]) if str(tag).startswith(prefix)),None)
        if tag:
            try:account_ids.append(int(tag.removeprefix(prefix)))
            except ValueError:pass
    with write_session() as session:
        names=dict(session.execute(select(Account.id,Account.username).where(Account.id.in_(account_ids))).all())
    rows=[]
    for item in items:
        tag=next((str(tag) for tag in item.get('tags',[]) if str(tag).startswith(prefix)),None)
        try:account_id=int(tag.removeprefix(prefix)) if tag else None
        except ValueError:account_id=None
        rows.append({'snapshot_id':item.get('id'),'account_id':account_id,'username':names.get(account_id),
            'created_at':item.get('time'),'paths':item.get('paths',[]),'tags':item.get('tags',[]),
            'kind':'logical_recovery_point','raw_objects':'Encrypted repository objects are intentionally hidden'})
    return {'destination':_destination_dict(destination),'entries':rows}


def queue_destination_operation(params):
    ident=storage._positive(params['id']);action=params.get('action')
    if action not in ('test','speed','reindex'):raise ValidationError('Choose test, speed, or reindex')
    destination=_row(SnapshotDestination,ident)
    mapping=params.get('account_mapping') or {}
    if not isinstance(mapping,dict) or len(mapping)>500 or any(
            not str(key).isdigit() or not isinstance(value,str) for key,value in mapping.items()):
        raise ValidationError('Account mapping must map source account IDs to destination usernames')
    normalized_mapping={str(storage._positive(int(key))):validate_username(value) for key,value in mapping.items()}
    for username in normalized_mapping.values():_account(username)
    if destination.status=='deleted':raise ValidationError('Destination configuration was removed')
    with write_session() as session:
        active=session.scalar(select(SnapshotDestinationOperation).where(
            SnapshotDestinationOperation.destination_id==ident,SnapshotDestinationOperation.status.in_(ACTIVE)))
        if active:raise ValidationError('A destination operation is already running')
        row=SnapshotDestinationOperation(destination_id=ident,action=action,options={'account_mapping':normalized_mapping})
        session.add(row);session.flush();result=_operation_dict(row);operation_id=row.id
    _executor.submit(execute_destination_operation,operation_id)
    return result


def _update_operation(ident,**values):
    with write_session() as session:
        row=session.get(SnapshotDestinationOperation,ident)
        if row is None:return
        for key,value in values.items():setattr(row,key,value)


def _reindex_destination(destination,repo,mapping=None):
    snapshots=storage.all_snapshots(repo)
    prefix=f'boron:{destination.namespace}:account:'
    with write_session() as session:
        accounts={row.id:row for row in session.scalars(select(Account)).all()}
        by_username={row.username:row for row in accounts.values()};mapping=mapping or {}
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
            mapped=by_username.get(mapping.get(str(account_id)))
            if mapped is None:
                unresolved.append({'snapshot_id':item.get('id'),'account_id':account_id});continue
            target_id=mapped.id
            if session.scalar(select(SnapshotRun.id).where(SnapshotRun.destination_id==destination.id,SnapshotRun.snapshot_id==item.get('id'))):continue
            when=item.get('time');started=utcnow()
            if isinstance(when,str):
                try:started=dt.datetime.fromisoformat(when.replace('Z','+00:00'))
                except ValueError:pass
            session.add(SnapshotRun(policy_id=policy.id,destination_id=destination.id,account_id=target_id,
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
                storage.check(repo);result=_destination_probe(repo,operation_id,64*1024)
            elif operation.action=='reindex':
                storage.check(repo);_update_operation(operation_id,progress_message='Reading repository manifests')
                result=_reindex_destination(destination,repo,(operation.options or {}).get('account_mapping'))
            else:
                _update_operation(operation_id,progress_message='Running bounded upload test')
                result=_destination_probe(repo,operation_id,4*1024*1024)
                elapsed=max(.001,time.monotonic()-started);result.update(
                    elapsed_seconds=round(elapsed,3),bytes_per_second=int(result['bytes']/elapsed))
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


def _destination_probe(repo, operation_id, size):
    """Bounded write/read/checksum/delete proof using only Boron's probe tag/path."""
    work=private_directory('probes',f'operation-{operation_id}');probe=work/'payload.bin';restored=work/'restored'
    snapshot_id=None
    try:
        probe.write_bytes(os.urandom(size));source_hash=hashlib.sha256(probe.read_bytes()).hexdigest()
        summary=storage.backup(repo,2147483647,[str(probe)],sandbox_roots=[str(work)])
        snapshot_id=summary['snapshot_id']
        output=storage.restore_to(repo,2147483647,snapshot_id,str(restored),selected_paths=[str(probe)])
        target=output/str(probe).lstrip('/')
        if not target.is_file() or not secrets.compare_digest(source_hash,hashlib.sha256(target.read_bytes()).hexdigest()):
            raise ValidationError('Destination probe download checksum did not match')
        storage.forget(repo,2147483647,[snapshot_id],prune=True);snapshot_id=None
        return {'verified':True,'write':True,'read':True,'delete':True,'sha256':source_hash,'bytes':size}
    finally:
        if snapshot_id:
            try:storage.forget(repo,2147483647,[snapshot_id],prune=True)
            except Exception:logger.exception('Could not clean destination probe snapshot %s',snapshot_id)
        shutil.rmtree(work,ignore_errors=True)


def account_catalog(params):
    now=utcnow()
    with write_session() as session:
        accounts=session.scalars(select(Account).order_by(Account.username)).all()
        runs=session.scalars(select(SnapshotRun).order_by(SnapshotRun.id.desc())).all()
        archives=session.scalars(select(BackupJob).order_by(BackupJob.id.desc())).all()
        policies=session.scalars(select(SnapshotPolicy).where(SnapshotPolicy.enabled==True)).all()
        destination_rows={row.id:row for row in session.scalars(select(SnapshotDestination)).all()}
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
            scheduled=[policy for policy in policies if policy.frequency!='manual' and
                (not policy.options.get('accounts') or account.username in policy.options.get('accounts',[])) and
                account.username not in policy.options.get('excluded_accounts',[])]
            snapshot_points=[row for kind,row in usable if kind=='snapshot']
            components=sorted(set(component for row in snapshot_points for component in row.options.get('components',[])))
            destination_ids=sorted(set(row.destination_id for row in snapshot_points))
            destinations=[destination_rows[ident].name for ident in destination_ids if ident in destination_rows]
            verified=[destination_rows[ident].last_verified_at for ident in destination_ids
                if ident in destination_rows and destination_rows[ident].last_verified_at]
            unavailable=any(ident not in destination_rows or not destination_rows[ident].enabled or destination_rows[ident].error
                for ident in destination_ids)
            overdue=False
            if latest and scheduled:
                thresholds=[int(policy.options.get('freshness_hours',max(1,FREQUENCIES.get(policy.frequency,86400)//3600)))
                    for policy in scheduled]
                overdue=bool(thresholds and age_hours is not None and age_hours>min(thresholds))
            if not usable:availability='no_backups' if scheduled or attempts else 'not_scheduled'
            elif unavailable:availability='destination_unavailable'
            elif attempt and attempt.status=='failed' and attempt is not latest:availability='partial'
            elif overdue:availability='overdue'
            else:availability='available'
            result.append({'account_id':account.id,'username':account.username,'primary_domain':account.primary_domain,
                'account_status':account.status,'account_group':'active' if account.status=='active' else 'former',
                'recovery_point_count':len(usable),
                'latest_recovery_at':(latest.completed_at or latest.started_at).isoformat() if latest else None,
                'latest_attempt_status':attempt.status if attempt else None,'age_hours':age_hours,
                'availability':availability,'components':components,'destinations':destinations,
                'last_verified_at':max(verified).isoformat() if verified else None})
        return {'accounts':result,'generated_at':now.isoformat(),
            'counts':{'active':sum(row['account_group']=='active' for row in result),
                      'former':sum(row['account_group']=='former' for row in result)}}


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
    from daemon.backup_notifications import EVENTS
    notification_events=_strings(params.get('notification_events',list(EVENTS)),'notification events')
    if not set(notification_events)<=set(EVENTS):raise ValidationError('Choose valid backup notification events')
    from shared.validation import validate_email_address
    recipients=_strings(params.get('notification_recipients',[]),'notification recipients',limit=25)
    recipients=list(dict.fromkeys(validate_email_address(value) for value in recipients))
    digest_frequency=params.get('digest_frequency','immediate')
    if digest_frequency not in ('immediate','daily','weekly'):raise ValidationError('Choose immediate, daily, or weekly notifications')
    quiesce_apps=params.get('quiesce_apps',False)
    if not isinstance(quiesce_apps,bool):raise ValidationError('Invalid application consistency option')
    retention = params.get('retention_count',7)
    if isinstance(retention,bool) or not isinstance(retention,int) or not 1<=retention<=365: raise ValidationError('Keep between 1 and 365 snapshots')
    mode = params.get('mode','incremental')
    if mode not in ('full','incremental','compressed','archive'): raise ValidationError('Choose full or incremental, compressed, or archive backup')
    destination_ids = params.get('destination_ids') or [params.get('destination_id')]
    if not isinstance(destination_ids,list) or not destination_ids or len(destination_ids)>8:
        raise ValidationError('Choose between one and eight destinations')
    destination_ids = list(dict.fromkeys(storage._positive(value) for value in destination_ids))
    timezone=str(params.get('timezone') or 'UTC')[:64]
    try:ZoneInfo(timezone)
    except ZoneInfoNotFoundError:raise ValidationError('Choose a valid IANA time zone') from None
    return dict(accounts=accounts,excluded_accounts=excluded,components=components,include_paths=includes,
        exclude_patterns=excludes,notification_channels=channels,notification_events=notification_events,
        notification_recipients=recipients,digest_frequency=digest_frequency,retention_count=retention,mode=mode,
        destination_ids=destination_ids,
        quiesce_apps=quiesce_apps,
        timezone=timezone,
        on_demand_retention=max(1,min(365,int(params.get('on_demand_retention',retention)))),
        pre_restore_retention=max(1,min(365,int(params.get('pre_restore_retention',retention)))),
        freshness_hours=max(1,min(8760,int(params.get('freshness_hours',36)))),
        minimum_free_mb=max(256,min(1048576,int(params.get('minimum_free_mb',2048)))),
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
    if any(not (row.capabilities or {}).get(options['mode'],
               options['mode']=='full' and (row.capabilities or {}).get('incremental',False))
           for row in destination_rows):
        raise ValidationError(f"{options['mode'].capitalize()} backups are not supported by every selected destination")
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
        cancel_requested=row.cancel_requested,pinned=row.pinned,
        started_at=row.started_at.isoformat(),completed_at=row.completed_at.isoformat() if row.completed_at else None)


def runs(params):
    account = _account(params['username']) if params.get('username') else None
    with write_session() as session:
        query = select(SnapshotRun).order_by(SnapshotRun.id.desc()).limit(200)
        if account: query=query.where(SnapshotRun.account_id==account.id)
        rows=session.scalars(query).all()
        if account and params.get('customer_scope'):
            visible_ids=set(session.scalars(select(SnapshotDestination.id).where(
                SnapshotDestination.customer_visible==True,SnapshotDestination.status!='deleted')))
            rows=[row for row in rows if row.destination_id in visible_ids]
        names=dict(session.execute(select(Account.id,Account.username).where(Account.id.in_([row.account_id for row in rows]))).all())
        destination_rows={row.id:row for row in session.scalars(select(SnapshotDestination).where(
            SnapshotDestination.id.in_([item.destination_id for item in rows]))).all()}
        result=[]
        for row in rows:
            destination=destination_rows.get(row.destination_id);item=_run_dict(row,customer=account is not None)
            item.update(username=names.get(row.account_id),destination_name=destination.name if destination else None,
                destination_kind=destination.kind if destination else None,
                destination_verified_at=destination.last_verified_at.isoformat() if destination and destination.last_verified_at else None,
                missing_components=sorted({'files','databases','mail','config'}-set(row.options.get('components',[]))))
            result.append(item)
        return {'runs':result}


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
        retry_options={**source.options,'queue_batch_id':uuid.uuid4().hex}
        row=SnapshotRun(policy_id=source.policy_id,destination_id=source.destination_id,account_id=source.account_id,
            options=retry_options,status='pending',trigger='retry')
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
        options=dict(policy.options);options['queue_batch_id']=uuid.uuid4().hex
        if not scheduled:options['retention_count']=options.get('on_demand_retention',options['retention_count'])
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
        ftp_accounts=session.scalars(select(FtpAccount).where(FtpAccount.account_id==account.id)).all()
        wordpress=session.scalars(select(WordPressInstall).where(WordPressInstall.account_id==account.id)).all()
        app_installs=session.scalars(select(AppInstall).where(AppInstall.account_id==account.id)).all()
        node_apps=session.scalars(select(NodeApp).where(NodeApp.account_id==account.id)).all()
        python_apps=session.scalars(select(PythonApp).where(PythonApp.account_id==account.id)).all()
        git_repos=session.scalars(select(GitRepo).where(GitRepo.account_id==account.id)).all()
        redis_instance=session.scalar(select(RedisInstance).where(RedisInstance.account_id==account.id))
    manifest={'format':1,'manifest_version':2,'account_id':account.id,'username':account.username,'php_version':account.php_version,
        'components':options['components'],'home':str(home),'domains':[{'domain':d.domain,'docroot':d.docroot,'kind':d.kind,'php_version':d.php_version} for d in domains],
        'databases':[{'name':d.db_name,'user':d.db_user} for d in databases],'mail_domains':[d.domain for d in mail_domains],
        'inventory':{
            'ftp_accounts':[{'login':item.ftp_login,'path':item.path,'credential':'password reset required'} for item in ftp_accounts],
            'ssl':[{'domain':item.domain,'status':item.ssl_status,'wildcard':item.ssl_is_wildcard,'recovery':'reissue'} for item in domains],
            'wordpress':[{'domain':item.domain,'path':item.path,'database':item.db_name,'version':item.wp_version} for item in wordpress],
            'applications':[{'kind':item.app_id,'domain':item.domain,'version':item.version,'database':item.db_name} for item in app_installs],
            'nodejs':[{'name':item.name,'domain':item.domain,'entry_point':item.entry_point,'version':item.node_version,'enabled':item.enabled,'configuration_restore':False} for item in node_apps],
            'python':[{'name':item.name,'domain':item.domain,'entry_point':item.entry_point,'app_type':item.app_type,'enabled':item.enabled,'configuration_restore':False} for item in python_apps],
            'git':[{'name':item.name,'deploy_target':item.deploy_target} for item in git_repos],
            'redis':({'memory_mb':redis_instance.mem_mb,'enabled':redis_instance.enabled,'data_persistence':'files component when enabled'} if redis_instance else None),
        },
        'recovery_capabilities':{'files':True,'databases_and_grants':True,'mail_and_settings':True,'dns':True,
            'cron':True,'php':True,'domains_metadata':True,'ftp_requires_password_reset':True,
            'ssl_reissued_instead_of_private_key_restore':True,'node_python_service_recreation':False}}
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


def _path_size(path):
    if path.is_symlink():return 0
    if path.is_file():return path.stat().st_size
    total=0
    for root,dirs,files in os.walk(path,followlinks=False):
        dirs[:]=[name for name in dirs if not (Path(root)/name).is_symlink()]
        for name in files:
            item=Path(root)/name
            try:
                if not item.is_symlink():total+=item.stat().st_size
            except FileNotFoundError:continue
    return total


def _portable_archive(run_id,account,paths,options):
    """Build a self-identifying tar artifact alongside restic's raw recovery data."""
    work=private_directory('exports',f'run-{run_id}')
    for item in work.iterdir():
        if item.is_dir() and not item.is_symlink():shutil.rmtree(item)
        else:item.unlink()
    mode=options['mode'];compressed=mode=='compressed'
    suffix='.boron.tar.gz' if compressed else '.boron.tar'
    artifact=work/f'{account.username}-{utcnow().strftime("%Y%m%d-%H%M%S")}{suffix}'
    estimated=sum(_path_size(Path(value)) for value in paths)
    free=shutil.disk_usage(work).free
    reserve=max(256*1024*1024,int(estimated*.1))
    if free < estimated+reserve:
        raise ValidationError(f'Portable archive needs about {estimated+reserve} bytes of staging space; only {free} bytes are free')
    home=Path(settings.home_base)/account.username;mail=Path(settings.mail_base)
    inventory=[]
    for value in paths:
        source=Path(value)
        if source==home:arcname=Path('account/home')
        elif source.is_relative_to(home):arcname=Path('account/home')/source.relative_to(home)
        elif source.is_relative_to(mail):arcname=Path('account/mail')/source.relative_to(mail)
        else:arcname=Path('account/metadata')
        inventory.append({'source':str(source),'archive_path':arcname.as_posix(),'size_bytes':_path_size(source)})
    manifest={'format':'boron-account-snapshot','format_version':1,'account_id':account.id,
        'username':account.username,'created_at':utcnow().isoformat(),'mode':mode,
        'components':options['components'],'inventory':inventory}
    manifest_path=work/'manifest.json';manifest_path.write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    patterns=options.get('exclude_patterns',[])
    def archive_filter(info):
        relative=info.name.removeprefix('account/')
        return None if any(fnmatch.fnmatch(relative,pattern) or fnmatch.fnmatch(Path(relative).name,pattern) for pattern in patterns) else info
    try:
        with tarfile.open(artifact,'w:gz' if compressed else 'w',format=tarfile.PAX_FORMAT) as archive:
            archive.add(manifest_path,arcname='account/manifest.json',recursive=False)
            for item,entry in zip(paths,inventory):archive.add(item,arcname=entry['archive_path'],recursive=True,filter=archive_filter)
    except (OSError,tarfile.TarError) as exc:
        raise ValidationError(f'Could not create portable account archive: {exc}') from exc
    digest=hashlib.sha256()
    with artifact.open('rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):digest.update(chunk)
    return artifact,{'name':artifact.name,'path':str(artifact),'size_bytes':artifact.stat().st_size,
        'sha256':digest.hexdigest(),'format':'tar.gz' if compressed else 'tar'}


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
    from daemon import backup_notifications
    siblings=[];batch_id=row.options.get('queue_batch_id')
    event='backup.completed' if row.status=='completed' else 'backup.failed'
    notification_job_id=row.id
    if batch_id:
        with write_session() as session:
            siblings=[item for item in session.scalars(select(SnapshotRun).where(
                SnapshotRun.account_id==row.account_id,SnapshotRun.policy_id==row.policy_id)).all()
                if item.options.get('queue_batch_id')==batch_id]
        if any(item.status in ACTIVE for item in siblings):return
        statuses={item.status for item in siblings};notification_job_id=min(item.id for item in siblings)
        if 'completed' in statuses and statuses & {'failed','cancelled'}:event='backup.partial'
        elif statuses <= {'completed'}:event='backup.completed'
        else:event='backup.failed'
    result=backup_notifications.dispatch(event,account,row.options['notification_channels'],job_id=notification_job_id,
        policy_id=row.policy_id,error=row.error,detail=f'{len(siblings)} destination results' if siblings else None,
        notification_events=row.options.get('notification_events'),
        recipients=row.options.get('notification_recipients',[]),digest_frequency=row.options.get('digest_frequency','immediate'))
    if siblings:
        for item in siblings:_update(item.id,notification_results=result)
    else:_update(row.id,notification_results=result)


def retained_snapshot_ids(items,options,protected=()):
    """Select recent plus daily/weekly/monthly points; always retain one usable point."""
    def stamp(item):
        value=item.get('time')
        try:return dt.datetime.fromisoformat(value.replace('Z','+00:00'))
        except (AttributeError,ValueError):return dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    ordered=sorted(items,key=stamp,reverse=True)
    if not ordered:return set()
    keep={item['id'] for item in ordered[:max(1,int(options.get('retention_count',7)))]}
    tiers=[('day',int(options.get('retention_daily',0))),('week',int(options.get('retention_weekly',0))),('month',int(options.get('retention_monthly',0)))]
    for kind,count in tiers:
        if count<=0:continue
        buckets=set()
        for item in ordered:
            moment=stamp(item)
            bucket=(moment.year,moment.month,moment.day) if kind=='day' else ((moment.isocalendar().year,moment.isocalendar().week) if kind=='week' else (moment.year,moment.month))
            if bucket in buckets:continue
            buckets.add(bucket);keep.add(item['id'])
            if len(buckets)>=count:break
    keep.add(ordered[0]['id']);keep.update(protected)
    return keep


def execute_run(ident):
    row=_row(SnapshotRun,ident)
    account_id=row.account_id
    account=None
    should_notify=False;quiesced=[]
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
            if row.options.get('quiesce_apps'):
                from daemon import handlers_maintenance
                _update(ident,progress_message='Pausing website requests for a consistent recovery point')
                quiesced=handlers_maintenance.quiesce_account(account)
            _update(ident,status='running',progress_message='Preparing account files and databases')
            paths=sources(account,row.options)
            estimate=_preflight_capacity(paths,row.options)
            automatic_config=None
            if row.trigger=='scheduled':
                from daemon.snapshot_config import automatic_export
                automatic_config=automatic_export();paths.append(automatic_config['path'])
            if _row(SnapshotRun,ident).cancel_requested:
                _update(ident,status='cancelled',progress_message='Cancelled after preparation',completed_at=utcnow());return
            archive_path=None;archive_summary=None
            if row.options['mode'] in ('compressed','archive'):
                _update(ident,progress_message='Building portable account archive')
                archive_path,archive_summary=_portable_archive(ident,account,paths,row.options)
                paths=[*paths,str(archive_path)]
            _update(ident,progress_message='Saving encrypted recovery point')
            repo=repository(_row(SnapshotDestination,row.destination_id))
            summary=storage.backup(repo,account.id,paths,policy_id=row.policy_id,
                excludes=row.options['exclude_patterns'],full_scan=row.options['mode']=='full',
                exclude_mail_staging='mail' in row.options['components'],
                sandbox_roots=sandbox_roots(account, paths))
            summary={**summary,'preflight':estimate}
            if archive_summary:summary={**summary,'portable_archive':archive_summary}
            if automatic_config:summary={**summary,'configuration_export':automatic_config}
            if _row(SnapshotRun,ident).cancel_requested:
                _update(ident,status='cancelled',snapshot_id=summary['snapshot_id'],summary=summary,
                    progress_message='Backup point completed; later steps cancelled',completed_at=utcnow());return
            _update(ident,snapshot_id=summary['snapshot_id'],summary=summary,progress_message='Applying retention')
            items=[s for s in storage.snapshots(repo,account.id) if f'policy:{row.policy_id}' in s.get('tags',[])]
            with write_session() as session:
                protected=set(session.scalars(select(SnapshotRun.snapshot_id).where(
                    SnapshotRun.account_id==account.id, SnapshotRun.destination_id==row.destination_id,
                    SnapshotRun.pinned==True, SnapshotRun.snapshot_id.is_not(None))))
                active_download_runs=set(session.scalars(select(SnapshotDownload.run_id).where(
                    SnapshotDownload.account_id==account.id, SnapshotDownload.status.in_(ACTIVE))))
                protected.update(session.scalars(select(SnapshotRun.snapshot_id).where(
                    SnapshotRun.id.in_(active_download_runs), SnapshotRun.snapshot_id.is_not(None))))
            keep=retained_snapshot_ids(items,row.options,protected)
            obsolete=[s['id'] for s in items if s['id'] not in keep]
            if obsolete:
                storage.forget(repo,account.id,obsolete,prune=True)
                with write_session() as session:
                    expired=session.scalars(select(SnapshotRun).where(SnapshotRun.account_id==account.id,
                        SnapshotRun.destination_id==row.destination_id,SnapshotRun.snapshot_id.in_(obsolete))).all()
                    for old in expired:old.status='expired';old.progress_message='Removed by retention policy'
            from daemon.snapshot_restores import apply_safety_retention
            apply_safety_retention(repo, account.id, row.destination_id, row.policy_id,
                                   row.options.get('pre_restore_retention',row.options['retention_count']))
            _update(ident,status='completed',progress_message='Snapshot ready',completed_at=utcnow())
    except Exception as exc:
        logger.exception('Snapshot run %s failed',ident)
        _update(ident,status='failed',error=str(exc)[-3000:],progress_message='Backup failed',completed_at=utcnow())
    finally:
        if quiesced:
            try:
                from daemon import handlers_maintenance
                handlers_maintenance.restore_quiesced_account(account,quiesced)
            except Exception:logger.exception('Could not restore website availability after snapshot run %s',ident)
        export=Path(settings.snapshot_private_dir)/'exports'/f'run-{ident}'
        if export.exists() and not export.is_symlink():shutil.rmtree(export,ignore_errors=True)
        if should_notify and account is not None:_notify(_row(SnapshotRun,ident),account)


def _preflight_capacity(paths,options):
    """Reject portable staging that would consume the configured free-space reserve."""
    reserve=int(options.get('minimum_free_mb',2048))*1024*1024;estimated=0
    if options.get('mode') in ('compressed','archive'):
        for value in paths:
            path=Path(value)
            if path.is_file() and not path.is_symlink():estimated+=path.stat().st_size
            elif path.is_dir() and not path.is_symlink():
                for root,_dirs,files in os.walk(path,followlinks=False):
                    for name in files:
                        item=Path(root)/name
                        try:
                            if not item.is_symlink():estimated+=item.stat().st_size
                        except FileNotFoundError:continue
        free=shutil.disk_usage(private_directory()).free
        if free-estimated<reserve:
            raise ValidationError(f'Portable backup needs about {estimated} bytes while preserving the configured free-space reserve')
    else:free=shutil.disk_usage(private_directory()).free
    if free<reserve:raise ValidationError('Backup staging free space is below the configured reserve')
    return {'estimated_source_bytes':estimated if estimated else None,'free_bytes':free,'reserve_bytes':reserve}


def _restore_entries(entries,account):
    home=Path(settings.home_base)/account.username
    return [{**row,'restore_path':str(Path(row['path']).relative_to(home)) if Path(row['path']).is_relative_to(home) and not Path(row['path']).is_relative_to(home/'.php') else None} for row in entries]


def browse(params):
    account=_account(params['username'])
    row=_row(SnapshotRun,params['run_id'])
    if row.account_id!=account.id or not row.snapshot_id:raise ValidationError('Snapshot not found for this account')
    destination=_row(SnapshotDestination,row.destination_id)
    if params.get('customer_scope') and not destination.customer_visible:
        raise ValidationError('This recovery point is not available in the customer panel')
    try:
        with lock(f'repository-{row.destination_id}',blocking=False):
            repo=repository(destination)
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


def _download_dict(row):
    return {'id':row.id,'run_id':row.run_id,'account_id':row.account_id,'status':row.status,
        'progress_message':row.progress_message,'filename':row.filename,'sha256':row.sha256,
        'size_bytes':row.size_bytes,'error':row.error,'created_at':row.created_at.isoformat(),
        'completed_at':row.completed_at.isoformat() if row.completed_at else None,
        'expires_at':row.expires_at.isoformat() if row.expires_at else None}


def _download_owned(params, *, require_ready=False):
    account=_account(params['username']);download=_row(SnapshotDownload,params['id'])
    if download.account_id!=account.id:raise ValidationError('Download not found for this account')
    run=_row(SnapshotRun,download.run_id);destination=_row(SnapshotDestination,run.destination_id)
    if params.get('customer_scope') and not destination.customer_visible:
        raise ValidationError('This recovery point is not available in the customer panel')
    if require_ready:
        now=utcnow();expires=download.expires_at
        if expires and expires.tzinfo is None:expires=expires.replace(tzinfo=dt.timezone.utc)
        path=Path(download.private_path or '')
        root=download_directory().resolve()
        if download.status!='ready' or not expires or expires<=now or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValidationError('This download is not ready or has expired')
    return account,download


def queue_download(params):
    cleanup_downloads()
    account=_account(params['username']);run_row=_row(SnapshotRun,params['run_id'])
    if run_row.account_id!=account.id or run_row.status!='completed' or not run_row.snapshot_id:
        raise ValidationError('A completed recovery point is required')
    destination=_row(SnapshotDestination,run_row.destination_id)
    if params.get('customer_scope') and not destination.customer_visible:
        raise ValidationError('This recovery point is not available in the customer panel')
    with write_session() as session:
        existing=session.scalar(select(SnapshotDownload).where(SnapshotDownload.run_id==run_row.id,
            SnapshotDownload.account_id==account.id,SnapshotDownload.status.in_(ACTIVE)).order_by(SnapshotDownload.id.desc()))
        if existing:return _download_dict(existing)
        row=SnapshotDownload(run_id=run_row.id,account_id=account.id)
        session.add(row);session.flush();result=_download_dict(row);ident=row.id
    _executor.submit(execute_download,ident)
    return result


def list_downloads(params):
    cleanup_downloads()
    account=_account(params['username'])
    with write_session() as session:
        rows=session.scalars(select(SnapshotDownload).where(SnapshotDownload.account_id==account.id)
            .order_by(SnapshotDownload.id.desc()).limit(100)).all()
        visible=[]
        for row in rows:
            run_row=session.get(SnapshotRun,row.run_id)
            destination=session.get(SnapshotDestination,run_row.destination_id) if run_row else None
            if params.get('customer_scope') and (not destination or not destination.customer_visible):continue
            visible.append(_download_dict(row))
        return {'downloads':visible}


def execute_download(ident):
    download=_row(SnapshotDownload,ident);work=download_directory(f'download-{ident}')
    try:
        with lock(f'download-{ident}'),lock(f'repository-{_row(SnapshotRun,download.run_id).destination_id}'):
            download=_row(SnapshotDownload,ident)
            if download.status!='pending':return
            run_row=_row(SnapshotRun,download.run_id);account=_row(Account,download.account_id)
            if run_row.status!='completed' or not run_row.snapshot_id:raise ValidationError('Recovery point is no longer available')
            destination=_row(SnapshotDestination,run_row.destination_id)
            _update_download(ident,status='running',progress_message='Restoring encrypted recovery point')
            portable=(run_row.summary or {}).get('portable_archive') or {}
            restored=storage.restore_to(repository(destination),account.id,run_row.snapshot_id,str(work/'restored'),
                selected_paths=[portable['path']] if portable.get('path') else ())
            if portable.get('path'):
                source=restored/portable['path'].lstrip('/');filename=portable.get('name') or f'{account.username}.boron.tar'
                if not source.is_file() or '/' in filename or '\0' in filename:raise ValidationError('Portable archive is missing')
                target=work/filename;shutil.move(source,target)
                expected=portable.get('sha256')
            else:
                _update_download(ident,progress_message='Building downloadable archive')
                filename=f'{account.username}-backup-{run_row.id}.boron.tar.gz';target=work/filename
                with tarfile.open(target,'w:gz') as archive:archive.add(restored,arcname='account',recursive=True)
                expected=None
            digest=hashlib.sha256()
            with target.open('rb') as handle:
                for chunk in iter(lambda:handle.read(1024*1024),b''):digest.update(chunk)
            checksum=digest.hexdigest()
            if expected and not secrets.compare_digest(checksum,expected):raise ValidationError('Portable archive checksum verification failed')
            try:
                api_user=pwd.getpwnam('boron-api');os.chown(target,api_user.pw_uid,api_user.pw_gid)
                os.chown(work,os.geteuid(),api_user.pw_gid);work.chmod(0o710)
            except KeyError:
                # Source-tree tests and development containers need not have service users.
                pass
            target.chmod(0o640)
            shutil.rmtree(work/'restored',ignore_errors=True)
            _update_download(ident,status='ready',progress_message='Download ready',filename=filename,
                private_path=str(target),sha256=checksum,size_bytes=target.stat().st_size,
                completed_at=utcnow(),expires_at=utcnow()+dt.timedelta(hours=24))
            from daemon import backup_notifications
            backup_notifications.dispatch('backup.download_ready',account,run_row.options.get('notification_channels',[]),
                job_id=run_row.id,policy_id=run_row.policy_id,detail=f'Download {filename} is ready for 24 hours',
                notification_events=run_row.options.get('notification_events'),
                recipients=run_row.options.get('notification_recipients',[]),
                digest_frequency=run_row.options.get('digest_frequency','immediate'))
    except Exception as exc:
        logger.exception('Snapshot download %s failed',ident);shutil.rmtree(work,ignore_errors=True)
        _update_download(ident,status='failed',progress_message='Download preparation failed',error=str(exc)[-3000:],completed_at=utcnow())


def _update_download(ident,**values):
    with write_session() as session:
        row=session.get(SnapshotDownload,ident)
        if row:
            for key,value in values.items():setattr(row,key,value)


def download_file(params):
    _,row=_download_owned(params,require_ready=True)
    return {'path':row.private_path,'filename':row.filename,'sha256':row.sha256,'size_bytes':row.size_bytes}


def cleanup_downloads():
    now=utcnow();removed=0
    with write_session() as session:
        rows=session.scalars(select(SnapshotDownload).where(SnapshotDownload.status=='ready')).all()
        for row in rows:
            path=Path(row.private_path or '')
            expires=row.expires_at
            if expires and expires.tzinfo is None:expires=expires.replace(tzinfo=dt.timezone.utc)
            missing=not path.is_file()
            if not missing and (not expires or expires>now):continue
            if path.is_file():path.unlink()
            parent=path.parent
            if parent.name==f'download-{row.id}':shutil.rmtree(parent,ignore_errors=True)
            row.status='expired' if not missing else 'failed';row.private_path=None
            row.progress_message='Download expired' if not missing else 'Prepared download file is missing; prepare it again'
            if missing:row.error='Private download staging was unavailable after restart'
            removed+=1
    return {'removed':removed}


def set_run_pin(params):
    row=_row(SnapshotRun,params['id'])
    if not isinstance(params.get('pinned'),bool):raise ValidationError('Invalid pinned value')
    if row.status not in ('completed','expired'):raise ValidationError('Only completed recovery points can be pinned')
    with write_session() as session:
        current=session.get(SnapshotRun,row.id);current.pinned=params['pinned'];session.flush();return _run_dict(current)


def run_scheduled():
    with write_session() as session:
        ids=list(session.scalars(select(SnapshotPolicy.id).where(SnapshotPolicy.enabled==True)))
    count=0
    for ident in ids:
        try:count+=len(queue_policy({'id':ident,'trigger':'scheduled'})['run_ids'])
        except Exception:logger.exception('Could not queue scheduled snapshot policy %s',ident)
    from daemon import backup_notifications
    check_health_alerts()
    backup_notifications.flush_digests()
    return count


def check_health_alerts():
    """Emit one deduplicated alert per currently unhealthy recovery point."""
    pending=[];now=utcnow()
    with write_session() as session:
        policies=session.scalars(select(SnapshotPolicy).where(SnapshotPolicy.enabled==True)).all()
        accounts=session.scalars(select(Account).where(Account.status=='active')).all()
        destinations={row.id:row for row in session.scalars(select(SnapshotDestination)).all()}
        runs=session.scalars(select(SnapshotRun).where(SnapshotRun.status=='completed')).all()
        for policy in policies:
            options=policy.options;destination_ids=options.get('destination_ids') or [policy.destination_id]
            selected=[account for account in accounts if (not options.get('accounts') or account.username in options['accounts'])
                and account.username not in options.get('excluded_accounts',[])]
            for account in selected:
                relevant=[row for row in runs if row.policy_id==policy.id and row.account_id==account.id]
                relevant.sort(key=lambda row:row.completed_at or row.started_at,reverse=True)
                latest=relevant[0] if relevant else None;threshold=int(options.get('freshness_hours',36))
                stamp=(latest.completed_at or latest.started_at) if latest else policy.last_queued_at
                if stamp:
                    if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=dt.timezone.utc)
                    if (now-stamp).total_seconds()>threshold*3600:
                        pending.append(('backup.overdue',account,policy,latest.id if latest else -(policy.id*1000000+account.id),
                            f'No usable recovery point within {threshold} hours'))
                for destination_id in destination_ids:
                    destination=destinations.get(destination_id)
                    if destination is None or not destination.enabled or destination.status!='ready' or destination.error:
                        pending.append(('backup.destination_unavailable',account,policy,
                            latest.id if latest else -(policy.id*1000000+account.id),f'Destination #{destination_id} is unavailable'))
    from daemon import backup_notifications
    for event,account,policy,job_id,detail in pending:
        options=policy.options
        backup_notifications.dispatch(event,account,options.get('notification_channels',[]),job_id=job_id,
            policy_id=policy.id,notification_events=options.get('notification_events'),
            recipients=options.get('notification_recipients',[]),digest_frequency=options.get('digest_frequency','immediate'),
            detail=detail)
    return {'alerts':len(pending)}


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
    with write_session() as session:
        downloads=session.scalars(select(SnapshotDownload).where(SnapshotDownload.status.in_(ACTIVE))).all()
    for item in downloads:
        if item.status=='pending':_executor.submit(execute_download,item.id)
        else:_update_download(item.id,status='failed',progress_message='Download preparation interrupted',
            error='Download worker was interrupted; prepare it again.',completed_at=utcnow())
    cleanup_downloads()
