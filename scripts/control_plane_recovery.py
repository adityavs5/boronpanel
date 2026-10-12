#!/usr/bin/env python3
"""Export or restore Boron's encrypted control-plane disaster-recovery archive.

The archive contains only the panel database and /etc/boron.  Customer home,
mail and database content remains the responsibility of account backups.
"""
from __future__ import annotations

import argparse
import datetime as dt
import grp
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from daemon.portable_crypto import decrypt, encrypt  # noqa: E402

FORMAT = "boron-control-plane"
FORMAT_VERSION = 2
MAX_CLEAR_BYTES = 512 * 1024 * 1024


def _passphrase(path_text: str) -> str:
    path = Path(path_text)
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError("recovery key file must be a regular file")
    if metadata.st_mode & 0o077:
        raise RuntimeError("recovery key file must not be accessible to group or other users")
    value = path.read_text(encoding="utf-8").strip()
    if len(value) < 16 or len(value) > 1024 or any(char in value for char in "\x00\r\n"):
        raise RuntimeError("recovery key must contain 16 to 1024 safe characters")
    return value


def _sqlite_copy(source: Path, target: Path) -> None:
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=15)
    try:
        target_connection = sqlite3.connect(target)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
    finally:
        source_connection.close()
    _sqlite_integrity(target)


def _sqlite_integrity(path: Path) -> None:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    if not row or row[0] != "ok":
        raise RuntimeError("control-plane database integrity check failed")


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _config_entries(root: Path) -> tuple[list[Path], list[Path]]:
    directories = []
    result = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeError(f"configuration contains a symbolic link: {path.relative_to(root)}")
        if stat.S_ISREG(metadata.st_mode):
            result.append(path)
        elif stat.S_ISDIR(metadata.st_mode):
            directories.append(path)
        else:
            raise RuntimeError(f"configuration contains an unsupported file type: {path.relative_to(root)}")
    return directories, result


def _identity_names(metadata: os.stat_result) -> dict:
    try:
        user = pwd.getpwuid(metadata.st_uid).pw_name
    except KeyError:
        user = None
    try:
        group = grp.getgrgid(metadata.st_gid).gr_name
    except KeyError:
        group = None
    return {"uid": metadata.st_uid, "gid": metadata.st_gid, "user": user, "group": group}


def _metadata_row(path: str, metadata: os.stat_result, *, entry_type: str, **extra) -> dict:
    return {
        "path": path,
        "type": entry_type,
        "mode": metadata.st_mode & 0o7777,
        **_identity_names(metadata),
        **extra,
    }


def export_archive(*, database: Path, config: Path, output: Path, passphrase: str,
                   version: str) -> dict:
    if output.exists():
        raise RuntimeError("refusing to overwrite an existing recovery archive")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="boron-control-export-") as temporary_text:
        temporary = Path(temporary_text)
        database_copy = temporary / "boron.db"
        _sqlite_copy(database, database_copy)
        source_database_metadata = database.stat()
        files = [_metadata_row(
            "database/boron.db", source_database_metadata, entry_type="file",
            sha256=_hash(database_copy), size=database_copy.stat().st_size,
        )]
        config_directories, config_files = _config_entries(config)
        for path in config_directories:
            relative = path.relative_to(config).as_posix()
            files.append(_metadata_row(
                f"etc-boron/{relative}", path.stat(), entry_type="directory", size=0,
            ))
        for path in config_files:
            relative = path.relative_to(config).as_posix()
            metadata = path.stat()
            files.append(_metadata_row(
                f"etc-boron/{relative}", metadata, entry_type="file",
                sha256=_hash(path), size=metadata.st_size,
            ))
        total = sum(int(item["size"]) for item in files)
        if total > MAX_CLEAR_BYTES:
            raise RuntimeError("control-plane export exceeds the 512 MB safety limit")
        manifest = {
            "format": FORMAT, "format_version": FORMAT_VERSION,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "panel_version": version,
            "source": {
                "hostname": socket.gethostname(),
                "machine": os.uname().machine,
                "system": os.uname().sysname,
                "release": os.uname().release,
            },
            "files": files,
        }
        clear_archive = temporary / "control-plane.tar.gz"
        with tarfile.open(clear_archive, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            archive.add(database_copy, arcname="database/boron.db", recursive=False)
            for path in config_directories:
                archive.add(path, arcname=f"etc-boron/{path.relative_to(config).as_posix()}", recursive=False)
            for path in config_files:
                archive.add(path, arcname=f"etc-boron/{path.relative_to(config).as_posix()}", recursive=False)
            data = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8") + b"\n"
            info = tarfile.TarInfo("manifest.json")
            info.size, info.mode = len(data), 0o600
            import io
            archive.addfile(info, io.BytesIO(data))
        encrypted_temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
        encrypted_temporary.unlink(missing_ok=True)
        try:
            encrypt(clear_archive, encrypted_temporary, passphrase)
            os.chmod(encrypted_temporary, 0o600)
            os.replace(encrypted_temporary, output)
        finally:
            encrypted_temporary.unlink(missing_ok=True)
    return {"path": str(output), "bytes": output.stat().st_size,
            "files": len(files), "sha256": _hash(output)}


def _validate_members(archive: tarfile.TarFile) -> None:
    total = 0
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
            raise RuntimeError("recovery archive contains an unsafe member")
        if not member.isfile() and not member.isdir():
            raise RuntimeError("recovery archive contains an unsupported member")
        total += max(0, member.size)
        if total > MAX_CLEAR_BYTES:
            raise RuntimeError("recovery archive exceeds the extraction safety limit")


def _load_manifest(root: Path) -> dict:
    manifest_path = root / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = document.get("format_version")
    if document.get("format") != FORMAT or version not in {1, FORMAT_VERSION}:
        raise RuntimeError("unsupported control-plane recovery archive")
    files = document.get("files")
    if not isinstance(files, list) or len(files) > 4096:
        raise RuntimeError("recovery manifest is invalid")
    expected = set()
    expected_directories = set()
    for row in files:
        relative = PurePosixPath(str(row.get("path", "")))
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("recovery manifest contains an unsafe path")
        path = root.joinpath(*relative.parts)
        entry_type = row.get("type", "file")
        if entry_type == "directory":
            if version == 1 or not path.is_dir() or path.is_symlink():
                raise RuntimeError(f"recovery archive verification failed for {relative}")
            expected_directories.add(relative.as_posix())
        elif entry_type == "file":
            if not path.is_file() or path.is_symlink() or _hash(path) != row.get("sha256"):
                raise RuntimeError(f"recovery archive verification failed for {relative}")
            expected.add(relative.as_posix())
        else:
            raise RuntimeError("recovery manifest contains an unsupported entry type")
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual - {"manifest.json"} != expected:
        raise RuntimeError("recovery archive contains undeclared files")
    if version == FORMAT_VERSION:
        actual_directories = {
            path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir()
        } - {"database", "etc-boron"}
        if actual_directories != expected_directories:
            raise RuntimeError("recovery archive contains undeclared directories")
    return document


def _resolve_owner(row: dict) -> tuple[int, int]:
    user = row.get("user")
    group = row.get("group")
    if not isinstance(user, str) or not isinstance(group, str):
        # Version 1 archives did not retain names. Root-owned entries remain
        # safe to recover; service-owned entries need a new export so numeric
        # IDs are never assigned to the wrong account on a fresh host.
        if int(row.get("uid", -1)) == 0 and int(row.get("gid", -1)) == 0:
            return 0, 0
        raise RuntimeError("legacy recovery archive lacks portable ownership metadata; create a new export")
    try:
        return pwd.getpwnam(user).pw_uid, grp.getgrnam(group).gr_gid
    except KeyError as exc:
        raise RuntimeError(f"required restore identity is not installed: {exc.args[0]}") from None


def _apply_metadata(root: Path, manifest: dict, *, prefix: str) -> None:
    rows = [row for row in manifest["files"] if str(row.get("path", "")).startswith(prefix)]
    rows.sort(key=lambda row: str(row["path"]).count("/"), reverse=True)
    for row in rows:
        relative = PurePosixPath(str(row["path"])).relative_to(PurePosixPath(prefix.rstrip("/")))
        path = root.joinpath(*relative.parts)
        _apply_path_metadata(path, row)


def _apply_path_metadata(path: Path, row: dict) -> None:
    mode = int(row.get("mode", 0))
    if mode <= 0 or mode > 0o777 or mode & 0o022:
        raise RuntimeError(f"unsafe recovery mode for {row['path']}")
    uid, gid = _resolve_owner(row)
    os.chown(path, uid, gid, follow_symlinks=False)
    os.chmod(path, mode, follow_symlinks=False)


def _accounts(database: Path) -> list[tuple[str, int]]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=10)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "accounts" not in tables:
            return []
        columns = {row[1] for row in connection.execute("PRAGMA table_info(accounts)")}
        if not {"username", "uid"} <= columns:
            return []
        status_filter = " AND status != 'terminated'" if "status" in columns else ""
        return [(str(name), int(uid)) for name, uid in connection.execute(
            "SELECT username, uid FROM accounts WHERE uid IS NOT NULL"
            f"{status_filter} ORDER BY username"
        )]
    finally:
        connection.close()


def _check_uid_conflicts(accounts: list[tuple[str, int]]) -> None:
    for username, uid in accounts:
        try:
            by_uid = pwd.getpwuid(uid)
        except KeyError:
            by_uid = None
        if by_uid and by_uid.pw_name != username:
            raise RuntimeError(f"UID {uid} required by {username} belongs to another local account")
        try:
            by_name = pwd.getpwnam(username)
        except KeyError:
            by_name = None
        if by_name and by_name.pw_uid != uid:
            raise RuntimeError(f"account {username} exists with a different UID")


def _ensure_accounts(accounts: list[tuple[str, int]], *, apply: bool) -> list[str]:
    created = []
    for username, uid in accounts:
        try:
            pwd.getpwnam(username)
            continue
        except KeyError:
            pass
        created.append(username)
        if apply:
            subprocess.run([
                "useradd", "--uid", str(uid), "--create-home", "--home-dir", f"/home/{username}",
                "--shell", "/bin/bash", username,
            ], check=True, timeout=60)
    return created


def restore_archive(*, archive_path: Path, passphrase: str, database: Path,
                    config: Path, overwrite_live: bool, apply: bool = True) -> dict:
    with tempfile.TemporaryDirectory(prefix="boron-control-restore-") as temporary_text:
        temporary = Path(temporary_text)
        clear = temporary / "control-plane.tar.gz"
        decrypt(archive_path, clear, passphrase)
        extracted = temporary / "content"
        extracted.mkdir()
        with tarfile.open(clear, "r:gz") as archive:
            _validate_members(archive)
            archive.extractall(extracted, filter="data")
        manifest = _load_manifest(extracted)
        restored_db = extracted / "database" / "boron.db"
        _sqlite_integrity(restored_db)
        accounts = _accounts(restored_db)
        _check_uid_conflicts(accounts)
        live_has_data = database.exists() and database.stat().st_size > 0
        # Validation is read-only: it decrypts into a private temporary tree,
        # checks the archive/database and reports the reconciliation plan.
        # Require the explicit overwrite flag only when a write can occur.
        if apply and live_has_data and not overwrite_live:
            raise RuntimeError("refusing to overwrite a live installation without --overwrite-live")
        planned_users = _ensure_accounts(accounts, apply=False)
        if not apply:
            return {"validated": True, "accounts": len(accounts), "users_to_create": planned_users,
                    "source": manifest.get("source", {}), "panel_version": manifest.get("panel_version")}

        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        rescue_db = database.with_name(f"{database.name}.before-control-restore-{stamp}")
        rescue_config = config.with_name(f"{config.name}.before-control-restore-{stamp}")
        if database.exists():
            shutil.copy2(database, rescue_db)
        if config.exists():
            shutil.copytree(config, rescue_config, symlinks=False)
        try:
            _ensure_accounts(accounts, apply=True)
            config.parent.mkdir(parents=True, exist_ok=True)
            new_config = config.with_name(f".{config.name}.restore-{os.getpid()}")
            shutil.copytree(extracted / "etc-boron", new_config, symlinks=False)
            _apply_metadata(new_config, manifest, prefix="etc-boron/")
            if config.exists():
                shutil.rmtree(config)
            os.replace(new_config, config)
            database.parent.mkdir(parents=True, exist_ok=True)
            database_tmp = database.with_name(f".{database.name}.restore-{os.getpid()}")
            shutil.copy2(restored_db, database_tmp)
            database_row = next(row for row in manifest["files"] if row["path"] == "database/boron.db")
            _apply_path_metadata(database_tmp, database_row)
            production_target = database.resolve().parent == Path("/var/lib/boron")
            try:
                gid = (database.stat().st_gid if database.exists()
                       else grp.getgrnam("boron-api").gr_gid if production_target
                       else os.getegid())
            except (OSError, KeyError):
                gid = os.getegid()
            uid = 0 if production_target else os.geteuid()
            temporary_stat = database_tmp.stat()
            if (temporary_stat.st_uid, temporary_stat.st_gid) != (uid, gid):
                os.chown(database_tmp, uid, gid)
            os.chmod(database_tmp, 0o640)
            os.replace(database_tmp, database)
        except Exception:
            if rescue_db.exists():
                os.replace(rescue_db, database)
            if rescue_config.exists():
                if config.exists():
                    shutil.rmtree(config)
                os.replace(rescue_config, config)
            raise
    return {"restored": True, "accounts": len(accounts), "users_created": planned_users,
            "database_rescue": str(rescue_db) if rescue_db.exists() else None,
            "config_rescue": str(rescue_config) if rescue_config.exists() else None}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--database", default="/var/lib/boron/boron.db")
    export_parser.add_argument("--config", default="/etc/boron")
    export_parser.add_argument("--output", required=True)
    export_parser.add_argument("--recovery-key-file", required=True)
    export_parser.add_argument("--panel-version", default="unknown")
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--archive", required=True)
    restore_parser.add_argument("--database", default="/var/lib/boron/boron.db")
    restore_parser.add_argument("--config", default="/etc/boron")
    restore_parser.add_argument("--recovery-key-file", required=True)
    restore_parser.add_argument("--overwrite-live", action="store_true")
    restore_parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    passphrase = _passphrase(args.recovery_key_file)
    if args.command == "export":
        result = export_archive(
            database=Path(args.database), config=Path(args.config), output=Path(args.output),
            passphrase=passphrase, version=args.panel_version,
        )
    else:
        result = restore_archive(
            archive_path=Path(args.archive), passphrase=passphrase,
            database=Path(args.database), config=Path(args.config),
            overwrite_live=args.overwrite_live, apply=not args.dry_run,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
