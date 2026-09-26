"""Typed, durable package maintenance jobs for the supported hosting stack."""
from __future__ import annotations

import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import select

from daemon.procutil import run
from shared.db import write_session
from shared.models import StackJob, utcnow
from shared.validation import ValidationError

PHP_TARGETS = {"8.1", "8.2", "8.3", "8.4", "8.5"}
COMPONENTS = {"openlitespeed", "php", "mariadb"}
ACTIONS = {"install", "update"}
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stack-maintenance")


def _job(row: StackJob) -> dict:
    return {
        "id": row.id, "component": row.component, "action": row.action,
        "target": row.target, "status": row.status, "phase": row.phase,
        "progress_pct": row.progress_pct, "detail": row.detail, "error": row.error,
        "cancel_requested": row.cancel_requested,
        "created_at": row.created_at.isoformat(),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def _version(command: list[str], pattern: str | None = None) -> str | None:
    if not Path(command[0]).exists() and shutil.which(command[0]) is None:
        return None
    result = run(command, timeout=15)
    text = (result.stdout or result.stderr).strip()
    if not result.ok:
        return None
    if pattern:
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1) if match else text[:120]
    return text.splitlines()[0][:120] if text else None


def inventory(params: dict | None = None) -> dict:
    ols_version = _version(["/usr/local/lsws/bin/openlitespeed", "-v"], r"LiteSpeed/([^\s]+)")
    php = []
    for target in sorted(PHP_TARGETS):
        compact = target.replace(".", "")
        binary = f"/usr/local/lsws/lsphp{compact}/bin/lsphp"
        version = _version([binary, "-v"], r"PHP\s+([0-9.]+)")
        php.append({"target": target, "installed": version is not None, "version": version})
    mariadb = _version(["/usr/bin/mariadb", "--version"], r"Distrib\s+([0-9.]+)")
    with write_session() as session:
        jobs = session.scalars(select(StackJob).order_by(StackJob.created_at.desc()).limit(50)).all()
    return {
        "components": {
            "openlitespeed": {"installed": ols_version is not None, "version": ols_version, "targets": ["latest-supported"]},
            "php": {"installed": [row for row in php if row["installed"]], "targets": php},
            "mariadb": {"installed": mariadb is not None, "version": mariadb, "targets": ["10.11"]},
        },
        "jobs": [_job(row) for row in jobs],
        "policy": {
            "parallel_jobs": 1,
            "mariadb": "Boron supports the Ubuntu 24.04 MariaDB 10.11 series. Cross-series database upgrades require a separate migration workflow.",
        },
    }


def _packages(component: str, action: str, target: str) -> list[str]:
    if component == "openlitespeed":
        if target != "latest-supported":
            raise ValidationError("Unsupported OpenLiteSpeed target")
        return ["openlitespeed"]
    if component == "php":
        if target not in PHP_TARGETS:
            raise ValidationError("Unsupported PHP version")
        compact = target.replace(".", "")
        return [f"lsphp{compact}", f"lsphp{compact}-common", f"lsphp{compact}-mysql", f"lsphp{compact}-curl", f"lsphp{compact}-opcache", f"lsphp{compact}-intl", f"lsphp{compact}-zip"]
    if component == "mariadb":
        if target != "10.11":
            raise ValidationError("Only the certified MariaDB 10.11 series is supported")
        return ["mariadb-server", "mariadb-client"]
    raise ValidationError("Unsupported stack component")


def preview(params: dict) -> dict:
    component = str(params.get("component"))
    action = str(params.get("action", "update"))
    target = str(params.get("target"))
    if component not in COMPONENTS or action not in ACTIONS:
        raise ValidationError("Unsupported stack operation")
    packages = _packages(component, action, target)
    free = shutil.disk_usage("/").free
    blockers = []
    if free < 2 * 1024**3:
        blockers.append("At least 2 GiB of free disk space is required")
    if component == "mariadb" and target != "10.11":
        blockers.append("Cross-series MariaDB upgrades are not available")
    return {
        "component": component, "action": action, "target": target,
        "packages": packages, "free_bytes": free, "blockers": blockers,
        "impact": "The affected service may restart briefly after package validation.",
        "rollback": "Configuration is backed up before package changes. Package downgrades are not automatic.",
    }


def start(params: dict) -> dict:
    operation = preview(params)
    if operation["blockers"]:
        raise ValidationError("; ".join(operation["blockers"]))
    if not bool(params.get("confirm")):
        raise ValidationError("Stack maintenance requires confirm=true")
    with write_session() as session:
        active = session.scalar(select(StackJob.id).where(StackJob.status.in_(["queued", "running"])))
        if active:
            raise ValidationError("Another stack maintenance job is already active")
        row = StackJob(component=operation["component"], action=operation["action"], target=operation["target"])
        session.add(row)
        session.flush()
        job_id = row.id
        result = _job(row)
    _executor.submit(_run_job, job_id, operation["packages"])
    return result


def _set(job_id: int, **values) -> StackJob | None:
    with write_session() as session:
        row = session.get(StackJob, job_id)
        if row is None:
            return None
        for key, value in values.items():
            setattr(row, key, value)
        session.flush()
        return row


def _run_job(job_id: int, packages: list[str]) -> None:
    row = _set(job_id, status="running", phase="preflight", progress_pct=5, started_at=utcnow())
    if row is None:
        return
    try:
        if Path("/var/lib/dpkg/lock-frontend").exists():
            lock = run(["fuser", "/var/lib/dpkg/lock-frontend"], timeout=10)
            if lock.ok and lock.stdout.strip():
                raise RuntimeError("Another package manager is currently active")
        backup = Path("/var/backups/boron-stack")
        backup.mkdir(parents=True, exist_ok=True)
        archive = backup / f"stack-{job_id}.tar.gz"
        candidates = [path for path in ["/usr/local/lsws/conf", "/etc/mysql", "/etc/php"] if Path(path).exists()]
        if candidates:
            saved = run(["tar", "-czf", str(archive), *candidates], timeout=120)
            if not saved.ok:
                raise RuntimeError("Could not create the pre-change configuration backup")
        _set(job_id, phase="package-index", progress_pct=20, detail="Refreshing package metadata")
        update = run(["apt-get", "update"], timeout=900)
        if not update.ok:
            raise RuntimeError((update.stderr or update.stdout)[-2000:])
        command = ["apt-get", "install", "-y"]
        if row.action == "update":
            command.append("--only-upgrade")
        _set(job_id, phase="simulation", progress_pct=30, detail="Simulating the package transaction")
        simulation = run(
            [*command, "--simulate", *packages], timeout=300,
            env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
        )
        if not simulation.ok:
            raise RuntimeError("Package simulation failed: " + (simulation.stderr or simulation.stdout)[-2500:])
        with write_session() as session:
            current = session.get(StackJob, job_id)
            if current and current.cancel_requested:
                _set(job_id, status="cancelled", phase="cancelled", completed_at=utcnow())
                return
        _set(job_id, phase="installing", progress_pct=45, detail="Installing validated packages")
        install = run([*command, *packages], timeout=1800, env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
        if not install.ok:
            raise RuntimeError((install.stderr or install.stdout)[-3000:])
        _set(job_id, phase="validating", progress_pct=85, detail="Validating service configuration")
        if row.component == "openlitespeed":
            check = run(["/usr/local/lsws/bin/openlitespeed", "-t"], timeout=60)
            if not check.ok:
                raise RuntimeError("OpenLiteSpeed configuration validation failed after update")
            reload_result = run(["systemctl", "reload", "lsws"], timeout=60)
            if not reload_result.ok:
                raise RuntimeError("OpenLiteSpeed could not be reloaded after update")
        elif row.component == "mariadb":
            check = run(["systemctl", "is-active", "mariadb"], timeout=30)
            if not check.ok:
                raise RuntimeError("MariaDB is not active after maintenance")
        else:
            compact = row.target.replace(".", "")
            check = run([f"/usr/local/lsws/lsphp{compact}/bin/lsphp", "-v"], timeout=30)
            if not check.ok:
                raise RuntimeError("The selected PHP runtime did not validate")
        _set(job_id, status="completed", phase="complete", progress_pct=100, detail="Stack maintenance completed", completed_at=utcnow())
    except Exception as exc:
        _set(job_id, status="failed", phase="failed", error=str(exc)[:4000], completed_at=utcnow())


def get_job(params: dict) -> dict:
    with write_session() as session:
        row = session.get(StackJob, int(params["job_id"]))
        if row is None:
            raise ValidationError("Stack job not found")
        return _job(row)


def cancel(params: dict) -> dict:
    with write_session() as session:
        row = session.get(StackJob, int(params["job_id"]))
        if row is None:
            raise ValidationError("Stack job not found")
        if row.status not in {"queued", "running"}:
            return _job(row)
        row.cancel_requested = True
        row.detail = "Cancellation requested; the current package-manager transaction will finish safely"
        return _job(row)
