"""Queued mail-routing restore execution and conservative restart recovery."""
from pathlib import Path
import threading

from daemon import snapshot_jobs as jobs, snapshot_mail_routing_journal as journal
from daemon import snapshot_mail_routing_recovery as recovery, database_operations
from shared.db import write_session
from shared.models import Account, SnapshotDestination, SnapshotRestore, SnapshotRun, utcnow
from shared.validation import ValidationError

_timers = {}
_timer_lock = threading.Lock()


def schedule(ident):
    def enqueue():
        with _timer_lock: _timers.pop(ident, None)
        jobs._executor.submit(recover, ident)
    with _timer_lock:
        if ident in _timers: return
        timer = threading.Timer(5, enqueue); timer.daemon = True
        _timers[ident] = timer; timer.start()


def _update(ident, **values):
    from daemon.snapshot_restores import _update
    _update(ident, **values)


def _finish(ident, result):
    if result['state'] == 'waiting':
        _update(ident, progress_message='Waiting for the supervised mail operation')
        schedule(ident); return
    with write_session() as session:
        row = session.get(SnapshotRestore, ident)
        if row.status != 'running' or row.selection.get('kind') != 'mail_routing':
            raise ValidationError('Routing restore job changed before completion')
        rolled_back = result.get('rolled_back') is True
        row.summary = {**row.summary, 'routing_finalized': True, 'guards_released': True,
                       'rolled_back': rolled_back, 'mail_domains': result['domains'], 'mailboxes': result['mailboxes']}
        row.status = 'failed' if rolled_back else 'completed'
        row.progress_message = 'Previous mail settings restored after interruption' if rolled_back else 'Mail routing restored'
        row.error = 'The requested restore did not finish; its previous mail settings were recovered.' if rolled_back else None
        row.completed_at = utcnow()


def run(ident, account, row, repo, snapshot_id):
    """Invoked with account/repository locks; retain private work on every outcome."""
    from daemon.snapshot_restores import record_routing_safety
    from daemon.snapshot_mail_guard_config import verify
    verify()
    with database_operations.mutation_lock(blocking=True):
        if row.selection.get('source_restore_id'):
            saved = recovery.load_previous(repo, account, snapshot_id, row.selection['source_restore_id'])
            plan = recovery.prepare_undo(account, saved)
        else:
            selected = recovery.load_routing(repo, account, snapshot_id, row.selection['mail_domains'])
            plan = recovery.prepare(account, selected)
        safety = recovery.save_previous(repo, account, ident, plan['previous'])
        record_routing_safety(ident, safety['snapshot_id'])
        path = journal.create(repo, account, ident, plan, safety['snapshot_id'])
        current = jobs._row(SnapshotRestore, ident)
        _update(ident, summary={**current.summary, 'routing_journal_created': True}, progress_message='Preparing mail routing switch')
        journal.acquire_guards(account, path)
    journal.launch(account, path)
    _finish(ident, journal.finalize(repo, account, path))


def failed(ident):
    """The parent releases its locks before invoking recovery after an error."""
    row = jobs._row(SnapshotRestore, ident)
    if row.status == 'pending':
        _update(ident, status='failed', error='Routing restore failed before live changes.', completed_at=utcnow())
        return
    _update(ident, progress_message='Inspecting interrupted mail-routing restore',
            error='Mail-routing restore requires verification; recovery copies and guards are retained.')
    recover(ident)


def recover(ident):
    from daemon.snapshot_restores import record_routing_safety
    row = jobs._row(SnapshotRestore, ident)
    if row.status != 'running' or row.selection.get('kind') != 'mail_routing': return
    source = jobs._row(SnapshotRun, row.run_id)
    try:
        with jobs.lock(f'account-{row.account_id}', blocking=False), jobs.lock(f'repository-{source.destination_id}', blocking=False):
            row = jobs._row(SnapshotRestore, ident)
            if row.status != 'running': return
            account = jobs._row(Account, row.account_id)
            repo = jobs.repository(jobs._row(SnapshotDestination, source.destination_id))
            original = Path(jobs.private_directory('restores', f'restore-{ident}')) / 'mail-routing-operation.json'
            if not original.exists():
                if row.summary.get('routing_journal_created'):
                    raise ValidationError('Expected routing journal is missing')
                _update(ident, status='failed', summary={**row.summary, 'routing_finalized': True},
                        error='Restore stopped before guard acquisition or live changes.', completed_at=utcnow())
                return
            payload = journal.read(account, original)
            record_routing_safety(ident, payload['safety_snapshot_id'])
            reverse = original.with_name('mail-routing-rollback-operation.json')
            path = reverse if reverse.exists() else original
            payload = journal.read(account, path)
            if path == original and payload['phase'] == 'prepared':
                _update(ident, status='failed', summary={**jobs._row(SnapshotRestore, ident).summary, 'routing_finalized': True},
                        error='Restore stopped before guard acquisition or live changes.', completed_at=utcnow())
                return
            if path == original and payload['phase'] == 'guarding': journal.acquire_guards(account, path)
            state = journal.recovery_state(account, path)
            if state['state'] == 'waiting': schedule(ident); return
            if payload.get('direction') == 'rollback': record_routing_safety(ident, payload['safety_snapshot_id'], purpose='rollback')
            if state['state'] in ('completed', 'finalizing') or state.get('phase') == 'verified':
                _finish(ident, journal.finalize(repo, account, path)); return
            if path != original:
                raise ValidationError('Interrupted rollback requires further recovery inspection')
            reverse = journal.prepare_rollback(repo, account, original)
            reverse_payload = journal.read(account, reverse)
            record_routing_safety(ident, reverse_payload['safety_snapshot_id'], purpose='rollback')
            journal.launch(account, reverse)
            _finish(ident, journal.finalize(repo, account, reverse))
    except BlockingIOError:
        schedule(ident)
    except Exception:
        # Launch may have timed out while its independent worker is still alive.
        # Reobserve that same journal/unit before deciding this job has failed.
        try:
            original = Path(jobs.private_directory('restores', f'restore-{ident}')) / 'mail-routing-operation.json'
            if original.exists():
                state = journal.recovery_state(jobs._row(Account, row.account_id), original)
                if state['state'] in ('waiting', 'completed', 'finalizing') or state.get('phase') == 'verified':
                    schedule(ident)
                    return
        except Exception:
            pass
        _update(ident, status='failed', progress_message='Mail-routing recovery needs inspection',
                error='Recovery could not verify completion. Encrypted safety copies, private journals and guards are retained.',
                completed_at=utcnow())
