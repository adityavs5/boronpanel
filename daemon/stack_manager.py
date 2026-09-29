"""Typed, durable package maintenance jobs for the supported hosting stack."""
from __future__ import annotations

import os
import json
import re
import shutil
import threading
import time
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
UPDATE_STATE_PATH = Path("/var/lib/boron/stack-update-state.json")
UPDATE_INTERVAL_SECONDS = 24 * 60 * 60
_state_lock = threading.Lock()

PHP_PACKAGE_SUFFIXES = ("", "-common", "-mysql", "-curl", "-opcache", "-intl", "-zip")


def _read_update_state() -> dict:
    try:
        value = json.loads(UPDATE_STATE_PATH.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_update_state(value: dict) -> None:
    UPDATE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = UPDATE_STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, separators=(",", ":")))
    os.chmod(temporary, 0o600)
    os.replace(temporary, UPDATE_STATE_PATH)


def _apt_policy(package: str) -> dict:
    result = run(["apt-cache", "policy", package], timeout=30)
    if not result.ok:
        return {"package": package, "installed": None, "candidate": None, "available": False, "update_available": False}
    installed_match = re.search(r"^\s*Installed:\s*(\S+)", result.stdout, re.MULTILINE)
    candidate_match = re.search(r"^\s*Candidate:\s*(\S+)", result.stdout, re.MULTILINE)
    installed = installed_match.group(1) if installed_match and installed_match.group(1) != "(none)" else None
    candidate = candidate_match.group(1) if candidate_match and candidate_match.group(1) != "(none)" else None
    newer = False
    if installed and candidate:
        comparison = run(["dpkg", "--compare-versions", candidate, "gt", installed], timeout=10)
        newer = comparison.ok
    return {"package": package, "installed": installed, "candidate": candidate, "available": candidate is not None, "update_available": newer}


def _installed_packages(pattern: str) -> list[str]:
    result = run(["dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${binary:Package}\n", pattern], timeout=30)
    packages = set()
    for line in result.stdout.splitlines():
        status, separator, name = line.partition("\t")
        if separator and status.startswith("ii") and name.strip():
            packages.add(name.split(":", 1)[0].strip())
    return sorted(packages)


def _package_catalog(component: str, action: str, target: str) -> tuple[list[str], list[str]]:
    if component == "openlitespeed":
        if target != "latest-supported":
            raise ValidationError("Unsupported OpenLiteSpeed target")
        candidates = ["openlitespeed"]
    elif component == "php":
        if target not in PHP_TARGETS:
            raise ValidationError("Unsupported PHP version")
        compact = target.replace(".", "")
        if action == "update":
            candidates = _installed_packages(f"lsphp{compact}*")
        else:
            candidates = [f"lsphp{compact}{suffix}" for suffix in PHP_PACKAGE_SUFFIXES]
    elif component == "mariadb":
        if target != "10.11":
            raise ValidationError("Only the certified MariaDB 10.11 series is supported")
        candidates = _installed_packages("mariadb-*") if action == "update" else ["mariadb-server", "mariadb-client"]
        candidates = [name for name in candidates if not name.endswith("-dbgsym")]
    else:
        raise ValidationError("Unsupported stack component")
    policies = [_apt_policy(name) for name in candidates]
    packages = [row["package"] for row in policies if row["available"] and (action != "update" or row["update_available"])]
    skipped = [row["package"] for row in policies if not row["available"]]
    return packages, skipped


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
    update_state = _read_update_state()
    checked_at = float(update_state.get("checked_at_epoch") or 0)
    if not update_state.get("checking") and time.time() - checked_at >= UPDATE_INTERVAL_SECONDS:
        update_state = {**update_state, "checking": True}
        _write_update_state(update_state)
        _executor.submit(check_updates, {"refresh": True, "background": True})
    return {
        "components": {
            "openlitespeed": {"installed": ols_version is not None, "version": ols_version, "targets": ["latest-supported"]},
            "php": {"installed": [row for row in php if row["installed"]], "targets": php},
            "mariadb": {"installed": mariadb is not None, "version": mariadb, "targets": ["10.11"]},
        },
        "jobs": [_job(row) for row in jobs],
        "updates": update_state,
        "pending_update_count": int(update_state.get("pending_update_count") or 0),
        "policy": {
            "parallel_jobs": 1,
            "mariadb": "Boron supports the Ubuntu 24.04 MariaDB 10.11 series. Cross-series database upgrades require a separate migration workflow.",
        },
    }


def _packages(component: str, action: str, target: str) -> list[str]:
    return _package_catalog(component, action, target)[0]


def check_updates(params: dict | None = None) -> dict:
    params = params or {}
    with _state_lock:
        previous = _read_update_state()
        with write_session() as session:
            active = session.scalar(select(StackJob.id).where(StackJob.status.in_(["queued", "running"])))
        if active:
            result = {**previous, "checking": False, "error": "Update check deferred while stack maintenance is active"}
            _write_update_state(result)
            return result
        try:
            if bool(params.get("refresh")):
                refreshed = run(["apt-get", "update"], timeout=900)
                if not refreshed.ok:
                    raise RuntimeError((refreshed.stderr or refreshed.stdout)[-2000:])
            components = {}
            for component, target in (("openlitespeed", "latest-supported"), ("mariadb", "10.11")):
                packages, _ = _package_catalog(component, "update", target)
                components[component] = {"available": bool(packages), "packages": packages, "count": len(packages)}
            php = {}
            for target in sorted(PHP_TARGETS):
                if not _installed_packages(f"lsphp{target.replace('.', '')}*"):
                    continue
                packages, _ = _package_catalog("php", "update", target)
                php[target] = {"available": bool(packages), "packages": packages, "count": len(packages)}
            components["php"] = php
            pending = int(components["openlitespeed"]["available"]) + int(components["mariadb"]["available"]) + sum(int(row["available"]) for row in php.values())
            result = {
                "checking": False, "checked_at": utcnow().isoformat(), "checked_at_epoch": time.time(),
                "pending_update_count": pending, "components": components, "error": None,
            }
        except Exception as exc:
            result = {
                **previous, "checking": False, "checked_at": utcnow().isoformat(),
                "checked_at_epoch": time.time(), "error": str(exc)[:2000],
            }
        _write_update_state(result)
        return result


def preview(params: dict) -> dict:
    component = str(params.get("component"))
    action = str(params.get("action", "update"))
    target = str(params.get("target"))
    if component not in COMPONENTS or action not in ACTIONS:
        raise ValidationError("Unsupported stack operation")
    packages, skipped = _package_catalog(component, action, target)
    free = shutil.disk_usage("/").free
    blockers = []
    if free < 2 * 1024**3:
        blockers.append("At least 2 GiB of free disk space is required")
    if component == "mariadb" and action == "update":
        size_result = run(["du", "-sb", "/var/lib/mysql"], timeout=60)
        if size_result.ok:
            try:
                database_bytes = int(size_result.stdout.split()[0])
                if free < database_bytes * 1.25 + 2 * 1024**3:
                    blockers.append("Free disk space must cover a full MariaDB recovery backup plus 2 GiB working space")
            except (ValueError, IndexError):
                blockers.append("Could not calculate MariaDB backup space requirements")
    if component == "mariadb" and target != "10.11":
        blockers.append("Cross-series MariaDB upgrades are not available")
    if action == "update" and not packages:
        blockers.append("No package updates are currently available for this component")
    if action == "install" and not packages:
        blockers.append("No installable packages are available for this target")
    return {
        "component": component, "action": action, "target": target,
        "packages": packages, "skipped_unavailable_packages": skipped, "free_bytes": free, "blockers": blockers,
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
    _executor.submit(_run_job, job_id)
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


def _prepare_mariadb_backup(job_id: int) -> Path:
    backup_root = Path("/var/backups/boron-stack")
    backup_root.mkdir(parents=True, exist_ok=True)
    if shutil.which("mariadb-backup") is not None:
        target = backup_root / f"mariadb-{job_id}"
        if target.exists():
            raise RuntimeError(f"Refusing to overwrite existing pre-upgrade backup {target}")
        backup = run(["mariadb-backup", "--backup", f"--target-dir={target}"], timeout=3600)
        if not backup.ok:
            raise RuntimeError("MariaDB pre-upgrade backup failed: " + (backup.stderr or backup.stdout)[-2500:])
        prepared = run(["mariadb-backup", "--prepare", f"--target-dir={target}"], timeout=3600)
        if not prepared.ok:
            raise RuntimeError("MariaDB pre-upgrade backup could not be prepared: " + (prepared.stderr or prepared.stdout)[-2500:])
        return target

    # Ubuntu does not install mariadb-backup by default. Avoid installing a
    # newer backup binary before the recovery point exists; stream a complete
    # logical dump to a fixed root-owned path instead.
    target = backup_root / f"mariadb-{job_id}.sql"
    compressed = target.with_suffix(".sql.gz")
    if target.exists() or compressed.exists():
        raise RuntimeError(f"Refusing to overwrite existing pre-upgrade backup {compressed}")
    dump = run([
        "mariadb-dump", "--all-databases", "--single-transaction", "--quick",
        "--routines", "--events", "--triggers", "--hex-blob", f"--result-file={target}",
    ], timeout=3600)
    if not dump.ok or not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise RuntimeError("MariaDB logical recovery backup failed: " + (dump.stderr or dump.stdout)[-2500:])
    zipped = run(["gzip", "-9", str(target)], timeout=3600)
    if not zipped.ok or not compressed.exists():
        raise RuntimeError("MariaDB recovery backup was created but could not be compressed")
    os.chmod(compressed, 0o600)
    return compressed


def _run_job(job_id: int) -> None:
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
        packages, _skipped = _package_catalog(row.component, row.action, row.target)
        if row.action == "update" and not packages:
            _set(job_id, status="completed", phase="complete", progress_pct=100, detail="Already current; no package updates are available", completed_at=utcnow())
            check_updates({"refresh": False, "background": True})
            return
        if not packages:
            raise RuntimeError("No installable packages are available for this target")
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
        if row.component == "openlitespeed":
            precheck = run(["/usr/local/lsws/bin/openlitespeed", "-t"], timeout=60)
            if not precheck.ok:
                raise RuntimeError("OpenLiteSpeed configuration is invalid before the update; no packages were changed")
        if row.component == "mariadb" and row.action == "update":
            health = run(["mariadb-check", "--all-databases", "--check-upgrade", "--silent"], timeout=900)
            if not health.ok:
                raise RuntimeError("MariaDB pre-upgrade table check failed; no packages were changed: " + (health.stderr or health.stdout)[-2000:])
            _set(job_id, phase="backup", progress_pct=38, detail="Creating and preparing a full MariaDB recovery backup")
            backup_path = _prepare_mariadb_backup(job_id)
            _set(job_id, detail=f"Prepared recovery backup at {backup_path}")
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
            upgrade_needed = run(["mariadb-upgrade", "--check-if-upgrade-is-needed"], timeout=120)
            if upgrade_needed.returncode == 0:
                upgraded = run(["mariadb-upgrade"], timeout=1800)
                if not upgraded.ok:
                    raise RuntimeError("MariaDB schema upgrade failed: " + (upgraded.stderr or upgraded.stdout)[-2500:])
            elif upgrade_needed.returncode != 1:
                raise RuntimeError("Could not determine whether MariaDB system tables need an upgrade")
            tables = run(["mariadb-check", "--all-databases", "--check-upgrade", "--silent"], timeout=900)
            if not tables.ok:
                raise RuntimeError("MariaDB post-update table validation failed: " + (tables.stderr or tables.stdout)[-2500:])
        else:
            compact = row.target.replace(".", "")
            check = run([f"/usr/local/lsws/lsphp{compact}/bin/lsphp", "-v"], timeout=30)
            if not check.ok:
                raise RuntimeError("The selected PHP runtime did not validate")
        _set(job_id, status="completed", phase="complete", progress_pct=100, detail="Stack maintenance completed", completed_at=utcnow())
        check_updates({"refresh": False, "background": True})
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
