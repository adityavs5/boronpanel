"""Account-scoped snapshot restore jobs with pre-restore recovery points."""
from daemon.database_operations import serialized_worker
import json
import logging
from pathlib import Path
import shutil
import uuid

from sqlalchemy import select
from daemon import snapshot_jobs as jobs, snapshot_storage as storage
from daemon.procutil import run
from shared.config import settings
from shared.db import write_session
from shared.models import Account, BackupJob, DatabaseGrant, MailDomain, RestoreJob, SnapshotDestination, SnapshotRestore, SnapshotRun, utcnow
from shared.validation import ValidationError

logger=logging.getLogger('borond.snapshot_restores')

MAIL_PHASES = ('preparing', 'prepared', 'guarded', 'provisioned', 'staged', 'switching',
               'switched', 'safety_saved', 'completed')


def _mail_checkpoint(ident, phase, data):
    from shared.models import SnapshotMailRecovery
    from daemon.snapshot_mail_journal import _path
    import re
    if phase not in MAIL_PHASES or not isinstance(data, dict) or set(data) - {
            'work', 'journal', 'safety_snapshot_id', 'mailboxes', 'guards_released'}:
        raise ValidationError('Invalid mailbox restore checkpoint')
    with write_session() as session:
        job = session.get(SnapshotRestore, ident)
        if job is None or job.selection.get('kind') != 'mail' or job.status != 'running':
            raise ValidationError('Mailbox restore job is not running')
        checkpoint = session.get(SnapshotMailRecovery, ident)
        expected = 'preparing' if checkpoint is None else MAIL_PHASES[MAIL_PHASES.index(checkpoint.phase) + 1]
        if phase != expected:
            raise ValidationError('Mailbox restore checkpoint is out of sequence')
        if checkpoint is None:
            checkpoint = SnapshotMailRecovery(restore_id=ident, phase=phase)
            session.add(checkpoint)
        if phase != 'preparing':
            work = str(Path(data['work']))
            _path(Path(work) / 'checkpoint')
            if checkpoint.work is not None and checkpoint.work != work:
                raise ValidationError('Mailbox recovery work directory changed')
            checkpoint.work = work
        if 'journal' in data:
            journal = str(_path(Path(data['journal'])))
            if journal != str(Path(checkpoint.work) / 'switch.json') or (checkpoint.journal and checkpoint.journal != journal):
                raise ValidationError('Mailbox switch journal changed')
            checkpoint.journal = journal
        if 'safety_snapshot_id' in data:
            value = data['safety_snapshot_id']
            if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
                raise ValidationError('Invalid mailbox safety snapshot')
            if job.safety_snapshot_id and job.safety_snapshot_id != value:
                raise ValidationError('Mailbox safety snapshot changed')
            job.safety_snapshot_id = value
        if phase == 'completed':
            if not job.safety_snapshot_id or data.get('guards_released') is not True or type(data.get('mailboxes')) is not int or data['mailboxes'] <= 0:
                raise ValidationError('Mailbox restore lacks completion evidence')
            job.status = 'completed'
            job.summary = {'mailboxes': data['mailboxes'], 'guards_released': True}
            job.completed_at = utcnow()
        checkpoint.phase = phase
        checkpoint.updated_at = utcnow()
        job.progress_message = {'preparing': 'Preparing selected mailboxes', 'prepared': 'Mailbox copies prepared',
            'guarded': 'Protecting selected mailboxes', 'provisioned': 'Mailbox accounts ready',
            'staged': 'Mailbox copies ready to switch', 'switching': 'Switching selected mailboxes',
            'switched': 'Saving previous mailbox contents', 'safety_saved': 'Finalizing mailbox restore',
            'completed': 'Selected mailboxes restored'}[phase]


def _serialize(row):
    return dict(id=row.id,run_id=row.run_id,account_id=row.account_id,selection=row.selection,status=row.status,
        safety_snapshot_id=row.safety_snapshot_id,progress_message=row.progress_message,summary=row.summary,error=row.error,
        started_at=row.started_at.isoformat(),completed_at=row.completed_at.isoformat() if row.completed_at else None)


def _owned_run(username,ident,allow_expired=False):
    account=jobs._account(username)
    row=jobs._row(SnapshotRun,ident)
    if row.account_id!=account.id or not row.snapshot_id or (row.status=='expired' and not allow_expired):
        raise ValidationError('Snapshot not found for this account')
    return account,row


def list_restores(params):
    account=jobs._account(params['username'])
    with write_session() as session:
        rows=session.scalars(select(SnapshotRestore).where(SnapshotRestore.account_id==account.id).order_by(SnapshotRestore.id.desc()).limit(100)).all()
        return {'restores':[_serialize(row) for row in rows]}


def _paths(values):
    values=jobs._strings(values,'restore paths')
    for value in values:
        if Path(value).parts and Path(value).parts[0]=='.php':
            raise ValidationError('PHP runtime files are managed by the panel; use PHP settings instead')
        if Path(value).is_absolute() or '..' in Path(value).parts:
            raise ValidationError('Restore file paths must be relative to the account home')
    return values


def _recovery_metadata(repo, account, snapshot_id):
    from daemon.snapshot_db_metadata import read_metadata
    stage = Path(settings.snapshot_private_dir)/'sources'/f'account-{account.id}'
    path = stage/'database-recovery.json'
    nodes = storage.entries(repo, account.id, snapshot_id, str(stage))
    if not any(node.get('path') == str(path) and node.get('type') == 'file' for node in nodes):
        return {}
    work = jobs.private_directory('metadata', uuid.uuid4().hex)
    try:
        data = storage.restore_to(repo, account.id, snapshot_id, str(work/'data'), selected_paths=[str(path)])
        return read_metadata(data/str(path).lstrip('/'), account.username)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _mail_recovery_metadata(repo, account, snapshot_id):
    from daemon.snapshot_mail_metadata import read_mailboxes
    stage = Path(settings.snapshot_private_dir)/'sources'/f'account-{account.id}'
    path = stage/'mail-recovery.json'
    nodes = storage.entries(repo, account.id, snapshot_id, str(stage))
    if not any(node.get('path') == str(path) and node.get('type') == 'file' for node in nodes):
        return {}
    work = jobs.private_directory('metadata', uuid.uuid4().hex)
    try:
        data = storage.restore_to(repo, account.id, snapshot_id, str(work/'data'), selected_paths=[str(path)])
        return read_mailboxes(data/str(path).lstrip('/'), account.username)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def mailbox_options(params):
    from daemon import mail
    account, source = _owned_run(params['username'], params['run_id'])
    if 'mail' not in source.options['components']:
        return {'mailboxes': []}
    try:
        with jobs.lock(f'repository-{source.destination_id}', blocking=False):
            repo = jobs.repository(jobs._row(SnapshotDestination, source.destination_id))
            metadata = _mail_recovery_metadata(repo, account, source.snapshot_id)
    except BlockingIOError:
        raise ValidationError('This destination is busy. Try again shortly.') from None
    with write_session() as session:
        owners = {row.domain: row.account_id for row in session.scalars(
            select(MailDomain).where(MailDomain.domain.in_(list(metadata))))}
    options = []
    for domain, saved in metadata.items():
        reason = None
        if owners.get(domain) != account.id:
            reason = ('This mail domain is not currently owned by this account.' if domain in owners else
                      'Restore this account’s mail domain registration before restoring its mailboxes.')
        current = set()
        if reason is None:
            try:
                current = {row['local_part'] for row in mail.list_mailboxes(domain)}
            except ValueError:
                reason = 'The mail domain needs provisioning before restoring its mailboxes.'
        for local, mailbox in saved['mailboxes'].items():
            action = 'unavailable' if reason else ('existing' if local in current else 'recreate')
            options.append({'address': local+'@'+domain, 'domain': domain, 'local_part': local,
                            'quota_mb': mailbox['quota_mb'], 'active': mailbox['active'],
                            'available': reason is None, 'action': action, 'reason': reason})
    return {'mailboxes': sorted(options, key=lambda item: item['address'])}


def _database_state(account, name, metadata):
    from daemon import mariadb
    with write_session() as session:
        registration = session.scalar(select(DatabaseGrant).where(DatabaseGrant.db_name == name))
        if registration and registration.account_id != account.id:
            return 'unavailable', 'This database name is registered to another account.'
        entry = metadata.get(name)
        present = mariadb.database_exists(name)
        login = bool(registration and mariadb.user_exists(registration.db_user))
        pending = any(name in (item.summary or {}).get('reconstruction_pending', [])
            for item in session.scalars(select(SnapshotRestore).where(
                SnapshotRestore.account_id == account.id,
                SnapshotRestore.status.in_(['failed','running']))).all())
        if registration and present and login and not pending:
            return 'existing', None
        if not entry:
            return 'unavailable', 'This recovery point lacks metadata needed to recreate the deleted database.'
        if registration and registration.db_user != entry['user']:
            return 'unavailable', 'The database login registration has changed.'
        from daemon.snapshot_db_metadata import resource_state
        resources = resource_state(account.username, entry)
        if resources['database'] or resources['login']:
            if not registration:
                return 'unavailable', 'Database or login resources still exist; resolve the name conflict before reconstruction.'
            if resources['login'] and not resources['login_matches']:
                return 'unavailable', 'The surviving database login differs from the backed-up credentials.'
        other = session.scalar(select(DatabaseGrant.id).where(DatabaseGrant.db_user == entry['user'], DatabaseGrant.db_name != name))
        if other:
            return 'unavailable', 'Another database uses this login.'
    if resources['database'] or resources['login']:
        return 'repair', 'Repair missing database resources using the backed-up credentials.'
    return 'recreate', 'Deleted database: recreate with its backed-up login and password.'


def database_options(params):
    account,source=_owned_run(params['username'],params['run_id'])
    if 'databases' not in source.options['components']:return {'databases':[]}
    directory=Path(settings.snapshot_private_dir)/'sources'/f'account-{account.id}'/'databases'
    try:
        with jobs.lock(f'repository-{source.destination_id}',blocking=False):
            repo=jobs.repository(jobs._row(SnapshotDestination,source.destination_id))
            nodes=storage.entries(repo,account.id,source.snapshot_id,str(directory))
            metadata=_recovery_metadata(repo,account,source.snapshot_id)
    except BlockingIOError:
        raise ValidationError('This destination is busy. Try again shortly.') from None
    databases=[]
    for node in nodes:
        path=Path(node.get('path',''))
        if node.get('type')!='file' or path.parent!=directory or path.suffix!='.sql':continue
        name=path.stem
        try:
            from daemon.snapshot_databases import _database_name
            _database_name(name)
        except ValidationError:continue
        state,reason=_database_state(account,name,metadata) if name.startswith(account.username+'_') else ('unavailable','Database not found for this account')
        databases.append({'name':name,'size':node.get('size',0),'available':state!='unavailable','action':state,'reason':reason})
    return {'databases':sorted(databases,key=lambda item:item['name'])}


def trigger(params):
    safety=jobs._row(SnapshotRestore,params['_safety']) if params.get('_safety') else None
    account,source=_owned_run(params['username'],params['run_id'],allow_expired=safety is not None)
    if safety and (safety.account_id!=account.id or safety.run_id!=source.id or not safety.safety_snapshot_id):
        raise ValidationError('Pre-restore recovery point not found for this account')
    if account.status!='active':raise ValidationError('Reactivate the account before restoring its data')
    if params.get('confirmation')!=account.username:raise ValidationError('Type the account username to confirm this restore')
    kind=safety.selection['kind'] if safety else params.get('kind','files')
    if kind not in ('files','databases'):raise ValidationError('Unsupported snapshot restore type')
    paths=[] if safety else _paths(params.get('paths',[]))
    databases=[]
    if kind=='databases':
        if safety:
            databases=_owned_databases(account,safety.summary.get('safety_databases',safety.selection.get('databases',[])))
        else:
            databases=jobs._strings(params.get('databases',[]),'database names')
            options={item['name']:item for item in database_options({'username':account.username,'run_id':source.id})['databases']}
            if not databases or any(name not in options or not options[name]['available'] for name in databases):
                raise ValidationError('Database not found for this account or unavailable for reconstruction')
            databases=sorted(set(databases))
    if kind not in source.options['components']:raise ValidationError('This recovery point does not contain '+kind)
    with jobs.lock('queue'),write_session() as session:
        for model in (SnapshotRestore,SnapshotRun,BackupJob,RestoreJob):
            if session.scalar(select(model.id).where(model.account_id==account.id,model.status.in_(jobs.ACTIVE))):
                raise ValidationError('A backup or restore is already in progress for this account')
        selection={'kind':kind,'paths':paths}
        if databases:selection['databases']=databases
        if safety:selection['source_snapshot_id']=safety.safety_snapshot_id
        row=SnapshotRestore(run_id=source.id,account_id=account.id,selection=selection,status='pending')
        session.add(row);session.flush();result=_serialize(row)
    jobs._executor.submit(execute,row.id)
    return result


def undo(params):
    account=jobs._account(params['username'])
    row=jobs._row(SnapshotRestore,params['restore_id'])
    if row.account_id!=account.id or not row.safety_snapshot_id:
        raise ValidationError('Pre-restore recovery point not found for this account')
    return trigger({'username':account.username,'run_id':row.run_id,'confirmation':params.get('confirmation'),'_safety':row.id})


def _update(ident,**values):
    with write_session() as session:
        row=session.get(SnapshotRestore,ident)
        for key,value in values.items():setattr(row,key,value)


def apply_safety_retention(repo, account_id, destination_id, policy_id, keep):
    """Called under the account/repository locks after a successful backup.

    Failed/interrupted restores retain their recovery copies until resolved.
    Only known successful restores belonging to this policy are eligible;
    unrelated repository snapshots are never inferred to be disposable.
    """
    if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
        raise ValidationError('Keep at least one pre-restore recovery point')
    with write_session() as session:
        pairs = session.execute(select(SnapshotRestore, SnapshotRun.policy_id).join(
            SnapshotRun, SnapshotRun.id == SnapshotRestore.run_id).where(
                SnapshotRestore.account_id == account_id,
                SnapshotRun.destination_id == destination_id).order_by(SnapshotRestore.id.desc())).all()
    eligible = [row for row, policy in pairs if policy == policy_id
                and row.status == 'completed' and row.safety_snapshot_id]
    candidates = eligible[keep:]
    candidate_ids = {row.id for row in candidates}
    protected = {row.safety_snapshot_id for row, _ in pairs
                 if row.id not in candidate_ids and row.safety_snapshot_id}
    protected.update(row.selection.get('source_snapshot_id') for row, _ in pairs
                     if row.status in jobs.ACTIVE)
    obsolete = sorted({row.safety_snapshot_id for row in candidates} - protected)
    if not obsolete:
        return 0
    # A failed repository operation leaves the recovery metadata available.
    existing = {item['id'] for item in storage.snapshots(repo, account_id)}
    remaining = [ident for ident in obsolete if ident in existing]
    if remaining:
        storage.forget(repo, account_id, remaining, prune=True)
    with write_session() as session:
        for row in session.scalars(select(SnapshotRestore).where(
                SnapshotRestore.id.in_(candidate_ids),
                SnapshotRestore.safety_snapshot_id.in_(obsolete))).all():
            row.safety_snapshot_id = None
            row.summary = {**row.summary, 'safety_snapshot_expired': True}
    return len(obsolete)


def _restore_paths(account,snapshot,selection):
    home=Path(settings.home_base)/account.username
    if home.is_symlink() or home.resolve()!=home or not home.is_dir():raise ValidationError('Account home is not a regular directory')
    roots=[Path(p) for p in snapshot.get('paths',[]) if (Path(p)==home or Path(p).is_relative_to(home)) and not Path(p).is_relative_to(home/'.php')]
    if not roots:raise ValidationError('This recovery point has no account files')
    selected=[home/p for p in selection['paths']] if selection['paths'] else roots
    for path in selected:
        if not any(path==root or path.is_relative_to(root) for root in roots):
            raise ValidationError('Selected path is outside this recovery point')
        # Reject live parent symlinks before taking the pre-restore snapshot.
        if path.parent.resolve()!=path.parent:raise ValidationError('A restore target parent uses a symbolic link')
    # Avoid overlapping selections; a parent already restores its children.
    unique=[]
    for path in sorted(set(selected),key=lambda p:len(p.parts)):
        if not any(path==p or path.is_relative_to(p) for p in unique):unique.append(path)
    return home,unique


def _owned_databases(account, names):
    from daemon.snapshot_databases import _database_name
    names=jobs._strings(names,'database names')
    if not names:raise ValidationError('Select at least one database to restore')
    with write_session() as session:
        owned=set(session.scalars(select(DatabaseGrant.db_name).where(DatabaseGrant.account_id==account.id)).all())
    for name in names:
        _database_name(name)
        if name not in owned or not name.startswith(account.username+'_'):
            raise ValidationError('Database not found for this account')
    return sorted(set(names))


@serialized_worker
def _restore_databases(ident,account,row,repo,snapshot_id,work):
    from daemon import snapshot_databases as database, mariadb
    names=row.selection['databases']
    metadata={} if row.selection.get('source_snapshot_id') else _recovery_metadata(repo,account,snapshot_id)
    states={}
    for name in names:
        state,reason=_database_state(account,name,metadata)
        if state=='unavailable':raise ValidationError(reason)
        states[name]=state
    base=Path(settings.snapshot_private_dir)
    source=base/('database-safety' if row.selection.get('source_snapshot_id') else 'sources')/f'account-{account.id}'/'databases'
    paths=[source/f'{name}.sql' for name in names]
    data=storage.restore_to(repo,account.id,snapshot_id,str(work/'data'),selected_paths=[str(p) for p in paths])
    # Verify every selected source and current database before changing any data.
    for name,path in zip(names,paths):
        database._sql_file(data/str(path).lstrip('/'))
        if mariadb.database_exists(name):
            database.validate_supported_objects(name)
            database._validate_database_boundary(name)
    stage=jobs.private_directory('database-safety',f'account-{account.id}')
    dumps=stage/'databases'
    if dumps.exists():shutil.rmtree(dumps)
    dumps.mkdir(mode=0o700)
    _update(ident,progress_message='Saving current databases before restore')
    try:
        existing=[name for name in names if mariadb.database_exists(name)]
        for name in existing:database.dump_database(name,dumps/f'{name}.sql',stage)
        if existing:
            safety=storage.backup(repo,account.id,[str(dumps)])
            _update(ident,safety_snapshot_id=safety['snapshot_id'],summary={'safety_databases':existing})
        completed=[]
        reconstructed=[]
        for name,path in zip(names,paths):
            if states[name] in ('recreate','repair'):
                from daemon.snapshot_db_metadata import recreate_missing,repair_missing
                state,reason=_database_state(account,name,metadata)
                if state!=states[name]:raise ValidationError('Database reconstruction state changed; retry after reviewing ownership')
                with write_session() as session:
                    registration=session.scalar(select(DatabaseGrant).where(DatabaseGrant.db_name==name))
                    if registration is None:
                        session.add(DatabaseGrant(account_id=account.id,db_name=name,db_user=metadata[name]['user']))
                _update(ident,progress_message='Recreating database resources for '+name,
                    summary={'databases':completed.copy(),'reconstructed':reconstructed.copy(),'safety_databases':existing,'reconstruction_pending':[name]})
                (recreate_missing if state=='recreate' else repair_missing)(account.username,metadata[name])
                with write_session() as session:
                    for previous in session.scalars(select(SnapshotRestore).where(SnapshotRestore.account_id==account.id)).all():
                        pending=previous.summary.get('reconstruction_pending',[])
                        if name in pending:
                            previous.summary={**previous.summary,'reconstruction_pending':[n for n in pending if n!=name]}
                reconstructed.append(name)
                _update(ident,summary={'databases':completed.copy(),'reconstructed':reconstructed.copy(),'safety_databases':existing})
            _owned_databases(account,[name])
            _update(ident,progress_message='Restoring database '+name)
            database.restore_database(name,data/str(path).lstrip('/'),work,replace_tables=True)
            completed.append(name)
            _update(ident,summary={'databases':completed.copy(),'reconstructed':reconstructed.copy(),'safety_databases':existing})
        _update(ident,status='completed',progress_message='Selected databases restored',completed_at=utcnow())
    finally:
        shutil.rmtree(dumps,ignore_errors=True)


def execute(ident):
    row=jobs._row(SnapshotRestore,ident)
    source=jobs._row(SnapshotRun,row.run_id)
    work=None
    try:
        with jobs.lock(f'account-{row.account_id}'),jobs.lock(f'repository-{source.destination_id}'):
            row=jobs._row(SnapshotRestore,ident)
            if row.status!='pending':return
            account=jobs._row(Account,row.account_id)
            if account.status!='active':raise ValidationError('Account is no longer active')
            if not account.uid or not account.gid or account.uid<=0 or account.gid<=0:raise ValidationError('Account identity is not safe for a restore')
            _update(ident,status='running',progress_message='Verifying selected recovery point')
            repo=jobs.repository(jobs._row(SnapshotDestination,source.destination_id))
            snapshot_id=row.selection.get('source_snapshot_id') or source.snapshot_id
            snapshot=storage.owned_snapshot(repo,account.id,snapshot_id)
            if row.selection['kind']=='mail':
                from daemon.snapshot_mail_restore import run_restore
                if 'mail' not in source.options.get('components', []):
                    raise ValidationError('This recovery point does not contain mail')
                run_restore(account, repo, snapshot_id, row.selection.get('mailboxes', []), ident,
                            lambda phase, data: _mail_checkpoint(ident, phase, data))
                return
            work=jobs.private_directory('restores',f'restore-{ident}')
            if row.selection['kind']=='databases':
                _restore_databases(ident,account,row,repo,snapshot_id,work)
                return
            home,paths=_restore_paths(account,snapshot,row.selection)
            data=storage.restore_to(repo,account.id,snapshot_id,str(work/'data'),selected_paths=[str(p) for p in paths])
            _update(ident,progress_message='Saving current files before restore')
            current=[str(p) for p in paths if p.exists() or p.is_symlink()]
            if current:
                safety=storage.backup(repo,account.id,current)
                _update(ident,safety_snapshot_id=safety['snapshot_id'])
            _update(ident,progress_message='Restoring account files')
            payload=dict(uid=account.uid,gid=account.gid,home=str(home),source=str(data/str(home).lstrip('/')),
                paths=[str(p.relative_to(home)) for p in paths])
            result=run(['/usr/bin/nice','-n','10','/usr/bin/ionice','-c','2','-n','7','/usr/bin/python3','-I',str(Path(__file__).with_name('snapshot_file_worker.py'))],
                input_text=json.dumps(payload),timeout=3600)
            result.raise_if_failed('Apply file restore')
            summary=json.loads(result.stdout)
            _update(ident,status='completed',progress_message='Selected files restored; unrelated files retained',summary=summary,completed_at=utcnow())
    except Exception as exc:
        if row.selection.get('kind') == 'mail':
            from shared.models import SnapshotMailRecovery
            with write_session() as session:
                current = session.get(SnapshotRestore, ident)
                checkpoint = session.get(SnapshotMailRecovery, ident)
                if current.status != 'completed':
                    needs_recovery = checkpoint is not None and checkpoint.work is not None
                    current.status = 'running' if needs_recovery else 'failed'
                    current.progress_message = 'Mailbox recovery pending' if needs_recovery else 'Mailbox restore failed before activation'
                    current.error = ('Mailbox restore was interrupted. Recovery records and guards are retained for inspection.'
                                     if needs_recovery else 'Mailbox preparation failed before live mail changes.')
                    current.completed_at = None if needs_recovery else utcnow()
            logger.warning('Mailbox restore %s requires failure inspection', ident)
            return
        logger.exception('Snapshot restore %s failed',ident)
        _update(ident,status='failed',progress_message='Restore failed; review the error before retrying',error=str(exc)[-3000:],completed_at=utcnow())
    finally:
        if work:
            shutil.rmtree(work,ignore_errors=True)


def recover_mail_restore(ident):
    """Reconcile an interrupted mailbox job without replaying its switch."""
    from shared.models import SnapshotMailRecovery
    from daemon import snapshot_mail_restore as mail_restore, snapshot_mail_journal as journal
    from daemon.snapshot_mail_service import inspect_switch
    row = jobs._row(SnapshotRestore, ident)
    source = jobs._row(SnapshotRun, row.run_id)
    try:
        with jobs.lock(f'account-{row.account_id}', blocking=False), jobs.lock(f'repository-{source.destination_id}', blocking=False):
            with write_session() as session:
                row = session.get(SnapshotRestore, ident)
                checkpoint = session.get(SnapshotMailRecovery, ident)
                if row.status != 'running' or row.selection.get('kind') != 'mail':
                    return
            if checkpoint is None or checkpoint.work is None:
                _update(ident, status='failed', progress_message='Mailbox restore interrupted before activation',
                        error='Preparation was interrupted before live mailbox changes.', completed_at=utcnow())
                return
            path = journal._path(Path(checkpoint.work) / 'switch.json')
            if not path.exists():
                account = jobs._row(Account, row.account_id)
                mail_restore.abort_pre_switch(account, checkpoint.work, ident)
                _update(ident, status='failed', progress_message='Mailbox restore interrupted before switching',
                        error='No mailbox switch was applied. Preparation guards were released; staged copies are retained.',
                        completed_at=utcnow())
                return
            payload = journal.read(path)
            if payload['restore_id'] != ident:
                raise ValidationError('Mailbox recovery journal does not match its job')
            rollback_path = mail_restore.rollback_journal(path)
            observed = journal.read(rollback_path) if rollback_path else payload
            if inspect_switch(observed['operation_id'])['state'] == 'running':
                _update(ident, progress_message='Waiting for mailbox switch to finish')
                # Reobserve this exact operation; never launch another worker.
                import threading
                timer = threading.Timer(5, lambda: jobs._executor.submit(recover_mail_restore, ident))
                timer.daemon = True
                timer.start()
                return
            account = jobs._row(Account, row.account_id)
            if rollback_path or any(entry['state'] == 'ready' for entry in journal.inspect(path)):
                result = mail_restore.rollback_restore(account, path, ident)
                _update(ident, status='failed', summary=result, progress_message='Interrupted restore rolled back',
                        error='The restore was interrupted. Original mailbox directories are restored; prepared copies are retained.',
                        completed_at=utcnow())
                return
            repo = jobs.repository(jobs._row(SnapshotDestination, source.destination_id))
            work = Path(checkpoint.work)
            if not (work / 'release-intent.json').exists():
                safety = mail_restore.backup_displaced(account, repo, path, ident,
                                                       recover=(work / 'mail-safety.json').exists())
                if row.safety_snapshot_id and row.safety_snapshot_id != safety['snapshot_id']:
                    raise ValidationError('Mailbox recovery safety snapshot changed')
                _update(ident, safety_snapshot_id=safety['snapshot_id'], progress_message='Finalizing recovered mailbox restore')
            result = mail_restore.finalize(account, repo, path, ident)
            with write_session() as session:
                current = session.get(SnapshotRestore, ident)
                saved = session.get(SnapshotMailRecovery, ident)
                if current.status != 'running' or saved.work != checkpoint.work:
                    raise ValidationError('Mailbox recovery job changed during finalization')
                if current.safety_snapshot_id and current.safety_snapshot_id != result['safety_snapshot_id']:
                    raise ValidationError('Mailbox recovery safety snapshot changed')
                current.safety_snapshot_id = result['safety_snapshot_id']
                current.status = 'completed'
                current.summary = {'mailboxes': result['mailboxes'], 'guards_released': True}
                current.progress_message = 'Selected mailboxes restored; interrupted finalization recovered'
                current.error = None
                current.completed_at = utcnow()
                saved.phase = 'completed'
                saved.journal = str(path)
                saved.updated_at = utcnow()
    except BlockingIOError:
        return  # A live account/repository owner still has the operation.
    except Exception:
        logger.warning('Mailbox restore %s still requires recovery inspection', ident)
        _update(ident, progress_message='Mailbox recovery pending',
                error='Recovery could not yet verify a complete mailbox restore. Guards and recovery records are retained.')


def recover_restores():
    with write_session() as session:
        rows=session.scalars(select(SnapshotRestore).where(SnapshotRestore.status.in_(jobs.ACTIVE))).all()
    for row in rows:
        if row.status=='pending':jobs._executor.submit(execute,row.id);continue
        if row.selection.get('kind') == 'mail':
            jobs._executor.submit(recover_mail_restore, row.id)
            continue
        try:
            with jobs.lock(f'account-{row.account_id}',blocking=False):
                current=jobs._row(SnapshotRestore,row.id)
                if current.status=='running':
                    _update(row.id,status='failed',progress_message='Interrupted',
                        error='Restore worker was interrupted. Some selected data may have been restored; the pre-restore snapshot is retained.',completed_at=utcnow())
        except BlockingIOError:continue
