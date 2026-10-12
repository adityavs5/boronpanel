"""Bounded retention and monthly usage aggregation for control-plane data."""
from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path

from sqlalchemy import delete, func, select

from shared.config import settings
from shared.db import write_session
from shared.models import (
    ActionStep,
    AppInstallJob,
    AuditLog,
    BandwidthDaily,
    BandwidthDailyDomain,
    BulkActionJob,
    CommandRun,
    ControlTask,
    ImapMigrationJob,
    LoginFailureEvent,
    NamespaceMigrationJob,
    PanelConfigJob,
    RetentionSettings,
    StackJob,
    SupportBundleJob,
    UsageMonthlyAggregate,
    UsageSnapshot,
    WordPressJob,
    utcnow,
)
from shared.validation import ValidationError

DEFAULTS = {
    "audit_days": 400,
    "action_step_days": 90,
    "task_output_days": 90,
    "usage_raw_days": 90,
    "usage_monthly_days": 395,
    "bandwidth_days": 400,
    "daemon_log_days": 30,
    "support_bundle_days": 7,
}
MINIMUMS = {
    "audit_days": 90,
    "action_step_days": 30,
    "task_output_days": 30,
    "usage_raw_days": 30,
    "usage_monthly_days": 365,
    "bandwidth_days": 90,
    "daemon_log_days": 7,
    "support_bundle_days": 1,
}
MAX_DAYS = 3650
MAX_BATCH = 100
TERMINAL = ("completed", "failed", "cancelled", "canceled", "ready")
_ROTATED_LOG = re.compile(r"^[A-Za-z0-9_.-]+\.log(?:\.\d+|-[0-9]{8})(?:\.gz)?$")


def _row(db) -> RetentionSettings:
    row = db.get(RetentionSettings, 1)
    if row is None:
        row = RetentionSettings(id=1, **DEFAULTS)
        db.add(row)
        db.flush()
    return row


def _data(row: RetentionSettings) -> dict:
    return {
        **{key: getattr(row, key) for key in DEFAULTS},
        "investigation_hold": bool(row.investigation_hold),
        "minimums": dict(MINIMUMS),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_settings(_params: dict | None = None) -> dict:
    with write_session() as db:
        data = _data(_row(db))
    data["table_sizes"] = table_sizes()
    return data


def set_settings(params: dict) -> dict:
    with write_session() as db:
        row = _row(db)
        for key in DEFAULTS:
            if key not in params:
                continue
            try:
                value = int(params[key])
            except (TypeError, ValueError):
                raise ValidationError(f"{key} must be a whole number of days") from None
            minimum = MINIMUMS[key]
            if not minimum <= value <= MAX_DAYS:
                raise ValidationError(f"{key} must be between {minimum} and {MAX_DAYS} days")
            setattr(row, key, value)
        if "investigation_hold" in params:
            row.investigation_hold = bool(params["investigation_hold"])
        row.updated_at = utcnow()
        db.flush()
        return _data(row)


def _delete_batch(model, condition, *, batch_size: int) -> int:
    with write_session() as db:
        ids = list(db.scalars(select(model.id).where(condition).order_by(model.id).limit(batch_size)))
        if ids:
            db.execute(delete(model).where(model.id.in_(ids)))
        return len(ids)


def _drain(model, condition, *, batch_size: int, max_batches: int) -> int:
    removed = 0
    for _ in range(max_batches):
        count = _delete_batch(model, condition, batch_size=batch_size)
        removed += count
        if count < batch_size:
            break
    return removed


def _aggregate_usage(cutoff: dt.datetime, *, batch_size: int, max_batches: int) -> int:
    removed = 0
    for _ in range(max_batches):
        with write_session() as db:
            rows = list(db.scalars(
                select(UsageSnapshot)
                .where(UsageSnapshot.taken_at < cutoff)
                .order_by(UsageSnapshot.id)
                .limit(batch_size)
            ))
            if not rows:
                break
            grouped: dict[tuple[int, str], list[UsageSnapshot]] = {}
            for sample in rows:
                stamp = sample.taken_at
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=dt.timezone.utc)
                grouped.setdefault((sample.account_id, stamp.strftime("%Y-%m")), []).append(sample)
            for (account_id, month), samples in grouped.items():
                aggregate = db.scalar(select(UsageMonthlyAggregate).where(
                    UsageMonthlyAggregate.account_id == account_id,
                    UsageMonthlyAggregate.month == month,
                ))
                if aggregate is None:
                    aggregate = UsageMonthlyAggregate(
                        account_id=account_id,
                        month=month,
                        sample_count=0,
                        disk_peak_bytes=0,
                        inode_peak=0,
                        process_peak=0,
                    )
                    db.add(aggregate)
                aggregate.sample_count += len(samples)
                aggregate.disk_peak_bytes = max(
                    aggregate.disk_peak_bytes,
                    *(s.disk_home_bytes + s.disk_mail_bytes + s.disk_db_bytes for s in samples),
                )
                aggregate.inode_peak = max(aggregate.inode_peak, *(s.inode_count for s in samples))
                aggregate.process_peak = max(aggregate.process_peak, *(s.process_count for s in samples))
                aggregate.updated_at = utcnow()
            db.execute(delete(UsageSnapshot).where(UsageSnapshot.id.in_([row.id for row in rows])))
            removed += len(rows)
        if len(rows) < batch_size:
            break
    return removed


def _prune_support_files(cutoff: dt.datetime, *, batch_size: int) -> int:
    root = Path(settings.support_bundle_dir).resolve()
    removed = 0
    with write_session() as db:
        rows = list(db.scalars(
            select(SupportBundleJob)
            .where((SupportBundleJob.expires_at <= utcnow()) | (SupportBundleJob.created_at < cutoff))
            .order_by(SupportBundleJob.id)
            .limit(batch_size)
        ))
        for row in rows:
            if row.file_path:
                candidate = Path(row.file_path)
                try:
                    resolved = candidate.resolve()
                    if resolved.parent == root and not candidate.is_symlink():
                        candidate.unlink(missing_ok=True)
                except OSError:
                    pass
            db.delete(row)
            removed += 1
    return removed


def _prune_rotated_logs(cutoff: dt.datetime, *, batch_size: int) -> int:
    root = Path(settings.log_dir)
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0
    removed = 0
    cutoff_timestamp = cutoff.timestamp()
    for path in sorted(entries, key=lambda item: item.name):
        if removed >= batch_size:
            break
        if not _ROTATED_LOG.fullmatch(path.name):
            continue
        try:
            info = path.lstat()
            if path.is_symlink() or not path.is_file() or info.st_mtime >= cutoff_timestamp:
                continue
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def table_sizes(_params: dict | None = None) -> dict:
    models = (
        AuditLog, ActionStep, UsageSnapshot, UsageMonthlyAggregate,
        BandwidthDaily, BandwidthDailyDomain, CommandRun, ControlTask,
    )
    with write_session() as db:
        counts = {model.__tablename__: int(db.scalar(select(func.count()).select_from(model)) or 0) for model in models}
    try:
        database_bytes = os.path.getsize(settings.db_path)
    except OSError:
        database_bytes = None
    return {"database_bytes": database_bytes, "rows": counts}


def prune(params: dict | None = None) -> dict:
    params = params or {}
    batch_size = max(1, min(int(params.get("batch_size", 50)), MAX_BATCH))
    max_batches = max(1, min(int(params.get("max_batches", 20)), 100))
    now = utcnow()
    with write_session() as db:
        policy = _row(db)
        values = _data(policy)
    if values["investigation_hold"]:
        return {"held": True, "removed": {}, "batch_size": batch_size}

    removed: dict[str, int] = {}
    removed["action_steps"] = _drain(
        ActionStep, ActionStep.started_at < now - dt.timedelta(days=values["action_step_days"]),
        batch_size=batch_size, max_batches=max_batches,
    )
    removed["audit_log"] = _drain(
        AuditLog, AuditLog.created_at < now - dt.timedelta(days=values["audit_days"]),
        batch_size=batch_size, max_batches=max_batches,
    )
    removed["usage_samples"] = _aggregate_usage(
        now - dt.timedelta(days=values["usage_raw_days"]),
        batch_size=batch_size, max_batches=max_batches,
    )
    removed["usage_monthly"] = _drain(
        UsageMonthlyAggregate,
        UsageMonthlyAggregate.month < (now - dt.timedelta(days=values["usage_monthly_days"])).strftime("%Y-%m"),
        batch_size=batch_size, max_batches=max_batches,
    )
    bandwidth_cutoff = (now - dt.timedelta(days=values["bandwidth_days"])).date().isoformat()
    removed["bandwidth"] = _drain(
        BandwidthDaily, BandwidthDaily.date < bandwidth_cutoff,
        batch_size=batch_size, max_batches=max_batches,
    )
    removed["bandwidth_domains"] = _drain(
        BandwidthDailyDomain, BandwidthDailyDomain.date < bandwidth_cutoff,
        batch_size=batch_size, max_batches=max_batches,
    )
    task_cutoff = now - dt.timedelta(days=values["task_output_days"])
    task_models = (
        CommandRun, ControlTask, WordPressJob, AppInstallJob, ImapMigrationJob,
        BulkActionJob, NamespaceMigrationJob, PanelConfigJob, StackJob,
    )
    for model in task_models:
        removed[model.__tablename__] = _drain(
            model,
            (
                model.completed_at.is_not(None)
                & (model.completed_at < task_cutoff)
                & model.status.in_(TERMINAL)
            ),
            batch_size=batch_size,
            max_batches=max_batches,
        )
    removed["login_failure_events"] = _drain(
        LoginFailureEvent, LoginFailureEvent.occurred_at < now - dt.timedelta(days=30),
        batch_size=batch_size, max_batches=max_batches,
    )
    removed["support_bundles"] = _prune_support_files(
        now - dt.timedelta(days=values["support_bundle_days"]), batch_size=batch_size,
    )
    removed["rotated_logs"] = _prune_rotated_logs(
        now - dt.timedelta(days=values["daemon_log_days"]), batch_size=batch_size,
    )
    return {"held": False, "removed": removed, "batch_size": batch_size, "table_sizes": table_sizes()}
