"""Account-scoped snapshot restore jobs with pre-restore recovery points."""
import json
import logging
from pathlib import Path
import shutil

from sqlalchemy import select
from daemon import snapshot_jobs as jobs, snapshot_storage as storage
from daemon.procutil import run
from shared.config import settings
from shared.db import write_session
from shared.models import Account, BackupJob, RestoreJob, SnapshotDestination, SnapshotRestore, SnapshotRun, utcnow
from shared.validation import ValidationError

logger=logging.getLogger('borond.snapshot_restores')


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


def trigger(params):
    safety=jobs._row(SnapshotRestore,params['_safety']) if params.get('_safety') else None
    account,source=_owned_run(params['username'],params['run_id'],allow_expired=safety is not None)
    if safety and (safety.account_id!=account.id or safety.run_id!=source.id or not safety.safety_snapshot_id):
        raise ValidationError('Pre-restore recovery point not found for this account')
    if account.status!='active':raise ValidationError('Reactivate the account before restoring its files')
    if params.get('confirmation')!=account.username:raise ValidationError('Type the account username to confirm this restore')
    if params.get('kind','files')!='files':raise ValidationError('Unsupported snapshot restore type')
    paths=[] if safety else _paths(params.get('paths',[]))
    if 'files' not in source.options['components']:raise ValidationError('This recovery point does not contain account files')
    with jobs.lock('queue'),write_session() as session:
        for model in (SnapshotRestore,SnapshotRun,BackupJob,RestoreJob):
            if session.scalar(select(model.id).where(model.account_id==account.id,model.status.in_(jobs.ACTIVE))):
                raise ValidationError('A backup or restore is already in progress for this account')
        selection={'kind':'files','paths':paths}
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
            home,paths=_restore_paths(account,snapshot,row.selection)
            work=jobs.private_directory('restores',f'restore-{ident}')
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
        logger.exception('Snapshot restore %s failed',ident)
        _update(ident,status='failed',progress_message='Restore failed; review the error before retrying',error=str(exc)[-3000:],completed_at=utcnow())
    finally:
        if work:
            shutil.rmtree(work,ignore_errors=True)


def recover_restores():
    with write_session() as session:
        rows=session.scalars(select(SnapshotRestore).where(SnapshotRestore.status.in_(jobs.ACTIVE))).all()
    for row in rows:
        if row.status=='pending':jobs._executor.submit(execute,row.id);continue
        try:
            with jobs.lock(f'account-{row.account_id}',blocking=False):
                current=jobs._row(SnapshotRestore,row.id)
                if current.status=='running':
                    _update(row.id,status='failed',progress_message='Interrupted',
                        error='Restore worker was interrupted. Some files may have been restored; the pre-restore snapshot is retained.',completed_at=utcnow())
        except BlockingIOError:continue
