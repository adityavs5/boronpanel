"""Password-encrypted backup configuration export/import for disaster recovery."""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
from pathlib import Path
import secrets
import shutil

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import select

from daemon import snapshot_jobs as jobs
from daemon.appcrypto import decrypt_env, encrypt_env
from shared.db import write_session
from shared.models import Account, BackupTelegramSettings, SnapshotDestination, SnapshotPolicy, SnapshotRun
from shared.validation import ValidationError


def _b64(value):return base64.urlsafe_b64encode(value).decode('ascii')
def _unb64(value):
    try:return base64.urlsafe_b64decode(str(value).encode('ascii'))
    except Exception:raise ValidationError('Backup configuration contains invalid base64 data') from None


def _key(recovery_key,salt):
    if not isinstance(recovery_key,str) or not 20<=len(recovery_key)<=256 or any(c in recovery_key for c in '\0\r\n'):
        raise ValidationError('Enter the independently saved recovery key')
    return Scrypt(salt=salt,length=32,n=2**15,r=8,p=1).derive(recovery_key.encode())


def _secret_file(path):
    return path.read_text() if path.exists() and path.is_file() and not path.is_symlink() else None


def export_configuration(params=None):
    with write_session() as session:
        destinations=session.scalars(select(SnapshotDestination).where(SnapshotDestination.status!='deleted')).all()
        policies=session.scalars(select(SnapshotPolicy)).all()
        runs=session.scalars(select(SnapshotRun).where(SnapshotRun.status.in_(['completed','expired']))).all()
        accounts={row.id:row.username for row in session.scalars(select(Account)).all()}
        telegram=session.get(BackupTelegramSettings,1)
    destination_rows=[]
    for row in destinations:
        root=jobs.private_directory('repositories',row.namespace)
        item={'source_id':row.id,'name':row.name,'kind':row.kind,'path':row.path,'namespace':row.namespace,
            'connection':row.connection,'enabled':row.enabled,'customer_visible':row.customer_visible,
            'capabilities':row.capabilities,'repository_password':_secret_file(root/'password'),
            'ssh_key':_secret_file(root/'ssh_key'),'ssh_public_key':_secret_file(root/'ssh_key.pub'),
            'known_hosts':_secret_file(root/'known_hosts'),'rclone_config':_secret_file(root/'rclone.conf')}
        if (root/'s3_credentials.enc').exists():item['s3_credentials']=decrypt_env((root/'s3_credentials.enc').read_text())
        if (root/'transport_credentials.enc').exists():item['transport_credentials']=decrypt_env((root/'transport_credentials.enc').read_text())
        destination_rows.append(item)
    bundle={'format':'boron-backup-configuration','version':1,'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'destinations':destination_rows,
        'policies':[{'source_id':row.id,'name':row.name,'destination_id':row.destination_id,
            'options':row.options,'frequency':row.frequency} for row in policies],
        'catalog':[{'policy_id':row.policy_id,'destination_id':row.destination_id,
            'username':accounts.get(row.account_id),'snapshot_id':row.snapshot_id,'options':row.options,
            'summary':row.summary,'started_at':row.started_at.isoformat(),
            'completed_at':row.completed_at.isoformat() if row.completed_at else None}
            for row in runs if row.snapshot_id and accounts.get(row.account_id)],
        'telegram':({'enabled':False,'chat_id':telegram.chat_id,'events':telegram.events,
            'token':decrypt_env(telegram.token_enc).get('token','') if telegram.token_enc else ''} if telegram else None)}
    recovery_key=secrets.token_urlsafe(32);salt=os.urandom(16);nonce=os.urandom(12)
    ciphertext=AESGCM(_key(recovery_key,salt)).encrypt(nonce,json.dumps(bundle,separators=(',',':')).encode(),b'boron-backup-config-v1')
    envelope={'format':'boron-backup-configuration','version':1,'salt':_b64(salt),'nonce':_b64(nonce),'ciphertext':_b64(ciphertext)}
    return {'filename':f'boron-backup-configuration-{dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")}.json',
        'payload':json.dumps(envelope,indent=2),'recovery_key':recovery_key,
        'summary':{'destinations':len(destination_rows),'policies':len(policies),'catalog_entries':len(bundle['catalog'])}}


def _open(payload,recovery_key):
    try:envelope=json.loads(payload)
    except (TypeError,ValueError):raise ValidationError('Backup configuration file is invalid JSON') from None
    if envelope.get('format')!='boron-backup-configuration' or envelope.get('version')!=1:
        raise ValidationError('Unsupported backup configuration format')
    try:
        clear=AESGCM(_key(recovery_key,_unb64(envelope['salt']))).decrypt(
            _unb64(envelope['nonce']),_unb64(envelope['ciphertext']),b'boron-backup-config-v1')
        bundle=json.loads(clear)
    except Exception:raise ValidationError('Recovery key is incorrect or the configuration is corrupt') from None
    if bundle.get('format')!='boron-backup-configuration' or bundle.get('version')!=1:
        raise ValidationError('Unsupported decrypted backup configuration')
    return bundle


def import_configuration(params):
    bundle=_open(params.get('payload',''),params.get('recovery_key',''))
    destinations=bundle.get('destinations');policies=bundle.get('policies');catalog=bundle.get('catalog',[])
    if not isinstance(destinations,list) or not isinstance(policies,list) or len(destinations)>100 or len(policies)>500:
        raise ValidationError('Backup configuration inventory is invalid')
    names=[str(row.get('name','')) for row in destinations];namespaces=[str(row.get('namespace','')) for row in destinations]
    if len(set(names))!=len(names) or len(set(namespaces))!=len(namespaces):raise ValidationError('Backup configuration contains duplicate destinations')
    with write_session() as session:
        existing_names=set(session.scalars(select(SnapshotDestination.name).where(SnapshotDestination.name.in_(names))).all())
        existing_ns=set(session.scalars(select(SnapshotDestination.namespace).where(SnapshotDestination.namespace.in_(namespaces))).all())
        existing_policies=set(session.scalars(select(SnapshotPolicy.name).where(SnapshotPolicy.name.in_([str(row.get('name','')) for row in policies]))).all())
    preview={'destinations':len(destinations),'policies':len(policies),'catalog_entries':len(catalog),
        'conflicts':{'destinations':sorted(existing_names),'namespaces':sorted(existing_ns),'policies':sorted(existing_policies)},
        'jobs_will_be_disabled':True,'remote_data_will_not_be_modified':True,'reindex_required':len(destinations)}
    if params.get('apply') is not True:return {'status':'preview',**preview}
    if any(preview['conflicts'].values()):raise ValidationError('Resolve the reported name or repository conflicts before import')
    created=[]
    try:
        with jobs.lock('configuration'),write_session() as session:
            destination_map={}
            for item in destinations:
                if item.get('kind') not in ('local','ssh','sftp','s3','drive'):raise ValidationError('Configuration contains an unsupported destination')
                namespace=str(item.get('namespace',''))
                if not namespace or not namespace.replace('-','').isalnum():raise ValidationError('Configuration contains an invalid namespace')
                root=jobs.private_directory('repositories',namespace);created.append(root)
                jobs._secret(root/'password',str(item.get('repository_password','')).rstrip('\n')+'\n')
                for key,filename in [('ssh_key','ssh_key'),('ssh_public_key','ssh_key.pub'),('known_hosts','known_hosts'),('rclone_config','rclone.conf')]:
                    if item.get(key) is not None:jobs._secret(root/filename,str(item[key]))
                if item.get('ssh_key') is not None:(root/'ssh_key').chmod(0o600)
                if item.get('s3_credentials') is not None:jobs._secret(root/'s3_credentials.enc',encrypt_env(item['s3_credentials']))
                if item.get('transport_credentials') is not None:
                    jobs._secret(root/'transport_credentials.enc',encrypt_env(item['transport_credentials']))
                    jobs._secret(root/'ssh_askpass','#!/bin/sh\nprintf "%s\\n" "$BORON_SSH_PASSWORD"\n');(root/'ssh_askpass').chmod(0o700)
                row=SnapshotDestination(name=item['name'],kind=item['kind'],path=item['path'],namespace=namespace,
                    connection=item.get('connection',{}),status='draft',enabled=False,
                    customer_visible=bool(item.get('customer_visible',True)),capabilities=item.get('capabilities',{}),
                    error='Imported configuration: test and reindex before enabling')
                session.add(row);session.flush();destination_map[int(item['source_id'])]=row.id
            policy_map={}
            for item in policies:
                options=dict(item.get('options') or {})
                mapped=[destination_map[value] for value in options.get('destination_ids',[item['destination_id']])]
                options['destination_ids']=mapped
                row=SnapshotPolicy(name=item['name'],destination_id=mapped[0],options=options,
                    frequency=item.get('frequency','manual'),enabled=False)
                session.add(row);session.flush();policy_map[int(item['source_id'])]=row.id
            accounts=dict(session.execute(select(Account.username,Account.id)).all())
            imported_catalog=0;unresolved=[]
            for item in catalog:
                if item.get('username') not in accounts:
                    unresolved.append(item.get('username'));continue
                if int(item.get('policy_id',0)) not in policy_map or int(item.get('destination_id',0)) not in destination_map:continue
                def parsed(value):return dt.datetime.fromisoformat(value.replace('Z','+00:00')) if value else None
                session.add(SnapshotRun(policy_id=policy_map[int(item['policy_id'])],
                    destination_id=destination_map[int(item['destination_id'])],account_id=accounts[item['username']],
                    options=item.get('options',{}),status='completed',trigger='config_import',snapshot_id=item['snapshot_id'],
                    summary=item.get('summary',{}),progress_message='Imported catalog entry; destination verification pending',
                    started_at=parsed(item['started_at']),completed_at=parsed(item.get('completed_at'))))
                imported_catalog+=1
            telegram=bundle.get('telegram')
            if telegram and telegram.get('token'):
                row=session.get(BackupTelegramSettings,1) or BackupTelegramSettings(id=1)
                row.enabled=False;row.chat_id=str(telegram.get('chat_id',''));row.events=telegram.get('events',[])
                row.token_enc=encrypt_env({'token':telegram['token']});session.add(row)
        return {'status':'imported',**preview,'catalog_imported':imported_catalog,
            'unresolved_accounts':sorted(set(filter(None,unresolved)))}
    except Exception:
        for root in created:
            if root.exists() and not root.is_symlink():shutil.rmtree(root,ignore_errors=True)
        raise
