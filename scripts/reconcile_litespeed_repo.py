"""Replace the historical globally trusted LiteSpeed APT source on upgrades.

Only Boron's known legacy files are removed. Unknown keys or modified source
files are left untouched and make the reconciliation fail closed. The update
worker runs this before switching the panel version and keeps originals in
its protected backup directory.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable


REPO_LINE = (
    "deb [signed-by=/usr/share/keyrings/boron-litespeed-repository.asc] "
    "https://rpms.litespeedtech.com/debian/ noble main\n"
)
LEGACY_LINES = {
    "deb http://rpms.litespeedtech.com/debian/ noble main",
    "#deb http://rpms.litespeedtech.com/edge/debian/ noble main",
}
KEY_FINGERPRINTS = {
    "42259994257E19EB6A91CA853F6F627083084D0E",
    "A3873F30BEAF6C50080A95BED2809E5CDEAC6B27",
    "3E892522DB44E1B063D366C5011AA62DEDA1F085",
    "60786C98FDAF53E40AD21E7248A14A8E233B8D9C",
}
BUNDLED_FINGERPRINTS = {
    "3E892522DB44E1B063D366C5011AA62DEDA1F085",
    "60786C98FDAF53E40AD21E7248A14A8E233B8D9C",
}
LEGACY_FILES = (
    "etc/apt/sources.list.d/lst_debian_repo.list",
    "etc/apt/trusted.gpg.d/lst_debian_repo.gpg",
    "etc/apt/trusted.gpg.d/lst_repo.gpg",
)
KEY_TARGET = "usr/share/keyrings/boron-litespeed-repository.asc"
SOURCE_TARGET = "etc/apt/sources.list.d/boron-litespeed.list"


def _fingerprints(path: Path) -> set[str]:
    proc = subprocess.run(
        ["gpg", "--show-keys", "--with-colons", str(path)],
        capture_output=True, text=True, timeout=15, check=True,
    )
    return {line.split(":")[9] for line in proc.stdout.splitlines()
            if line.startswith("fpr:")}


def _validate_legacy(root: Path) -> None:
    source = root / LEGACY_FILES[0]
    if source.exists():
        lines = {line.strip() for line in source.read_text().splitlines() if line.strip()}
        if lines != LEGACY_LINES:
            raise RuntimeError("unknown LiteSpeed legacy source; inspect before migration")
    for name in LEGACY_FILES[1:]:
        key = root / name
        if key.exists() and not _fingerprints(key).issubset(KEY_FINGERPRINTS):
            raise RuntimeError("unknown legacy APT key; inspect before migration")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o644)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def reconcile(
    *, root: Path = Path("/"), backup_dir: Path,
    apt_update: Callable[[], None] | None = None,
) -> None:
    root = root.resolve()
    backup_dir = backup_dir.resolve()
    key_source = Path(__file__).resolve().parent.parent / "deploy/litespeed-repository-key.asc"
    if _fingerprints(key_source) != BUNDLED_FINGERPRINTS:
        raise RuntimeError("bundled LiteSpeed key fingerprint changed")
    if not (root / "usr/local/lsws").is_dir():
        return  # LiteSpeed is not installed on this machine.
    _validate_legacy(root)
    source = root / SOURCE_TARGET
    key = root / KEY_TARGET
    if source.exists() and source.read_text() != REPO_LINE:
        raise RuntimeError("LiteSpeed APT source was customized; inspect before migration")
    if key.exists() and key.read_bytes() != key_source.read_bytes():
        raise RuntimeError("LiteSpeed scoped key differs from bundled key")
    if (source.exists() and key.exists()
            and all(not (root / name).exists() for name in LEGACY_FILES)):
        return  # Already scoped; avoid an unnecessary network refresh on updates.

    watched = [root / name for name in (*LEGACY_FILES, KEY_TARGET, SOURCE_TARGET)]
    before = {path: path.read_bytes() if path.exists() else None for path in watched}
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_dir, 0o700)
    for path, content in before.items():
        if content is not None:
            backup = backup_dir / f"apt-{path.name}.before-scoped-trust"
            if backup.exists():
                raise RuntimeError(f"refusing to overwrite APT backup: {backup}")
            backup.write_bytes(content)
            os.chmod(backup, 0o600)

    try:
        _atomic_write(key, key_source.read_bytes())
        _atomic_write(source, REPO_LINE.encode())
        for name in LEGACY_FILES:
            (root / name).unlink(missing_ok=True)
        if apt_update is None:
            subprocess.run(["apt-get", "update", "-qq"], check=True, timeout=300)
        else:
            apt_update()
    except Exception:
        for path, content in before.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, content)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("requires root")
    reconcile(backup_dir=args.backup_dir)


if __name__ == "__main__":
    main()
