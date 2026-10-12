#!/usr/bin/python3
"""Detached, stdlib-only restore of one pre-update control-plane database.

The root helper launches this with systemd-run.  It validates the snapshot,
creates a rescue copy, stops only the two Boron control-plane services, swaps
the database atomically, starts the services, and restores the rescue copy if
the panel does not recover.  Customer-facing services are never touched.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import sqlite3
import ssl
import subprocess
import time
import urllib.request


def _write_status(path: Path, **values) -> None:
    current = {}
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    current.update(values, updated_at=dt.datetime.now(dt.timezone.utc).isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(current, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _integrity(path: Path) -> None:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    if not row or row[0] != "ok":
        raise RuntimeError("database integrity check failed")


def _online_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    source_db = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=15)
    try:
        destination_db = sqlite3.connect(temporary)
        try:
            source_db.backup(destination_db)
        finally:
            destination_db.close()
    finally:
        source_db.close()
    _integrity(temporary)
    try:
        ownership = target.stat() if target.exists() else source.stat()
        os.chown(temporary, ownership.st_uid, ownership.st_gid)
        os.chmod(temporary, ownership.st_mode & 0o777 or 0o640)
    except OSError:
        os.chmod(temporary, 0o640)
    os.replace(temporary, target)


def _services(systemctl: str, action: str, units: list[str]) -> bool:
    ok = True
    for unit in units:
        result = subprocess.run([systemctl, action, unit], capture_output=True, text=True, timeout=90)
        ok = ok and result.returncode == 0
    return ok


def _healthy(url: str, timeout: float) -> bool:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, context=context, timeout=5) as response:
                if response.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(2)
    return False


def run(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve(strict=True)
    target = Path(args.target).resolve()
    status = Path(args.status_file)
    rescue = Path(args.rescue)
    units = [item.strip() for item in args.units.split(",") if item.strip()]
    _write_status(status, state="validating", source=source.parent.name, target=str(target))
    _integrity(source)
    _online_copy(target, rescue)
    _write_status(status, state="stopping", rescue=str(rescue))
    _services(args.systemctl, "stop", units)
    restored = False
    try:
        _online_copy(source, target)
        restored = True
        _write_status(status, state="starting")
        if not _services(args.systemctl, "start", list(reversed(units))):
            raise RuntimeError("control-plane services did not start")
        if not _healthy(args.health_url, args.health_timeout):
            raise RuntimeError("restored control plane did not pass its health check")
        _write_status(status, state="completed", rolled_back=False)
        return 0
    except Exception as exc:  # last-resort recovery boundary
        if restored:
            _services(args.systemctl, "stop", units)
            try:
                _online_copy(rescue, target)
                _services(args.systemctl, "start", list(reversed(units)))
                recovered = _healthy(args.health_url, args.health_timeout)
            except Exception:
                recovered = False
        else:
            _services(args.systemctl, "start", list(reversed(units)))
            recovered = True
        _write_status(
            status, state="failed", error=f"{type(exc).__name__}: {str(exc)[:500]}",
            rolled_back=bool(restored), recovery_healthy=recovered,
        )
        return 1 if recovered else 2


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--rescue", required=True)
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--health-url", required=True)
    parser.add_argument("--health-timeout", type=float, default=90.0)
    parser.add_argument("--units", default="boron-api.service,boron-provisiond.service")
    parser.add_argument("--systemctl", default="systemctl")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
