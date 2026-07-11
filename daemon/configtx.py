"""Shared validate -> backup -> apply -> reload -> verify -> rollback engine.

ARCHITECTURE.md SS7. Every subsystem that writes a config file and reloads a
service (OLS vhosts in Phase b, Postfix/Dovecot maps in Phase e) plugs its
own validate/reload/verify callables into ConfigWriter.apply() rather than
reimplementing this sequence. This is the one mandatory safety net the
project goal calls out as "not optional."

PowerDNS does not use this module -- it has no local file, and validation/
atomicity is provided by PowerDNS's own REST API response codes (see
ARCHITECTURE.md SS7, "PowerDNS" paragraph).
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger("borond.configtx")


@dataclass
class StepResult:
    ok: bool
    message: str = ""


@dataclass
class ConfigTxResult:
    applied: bool
    rolled_back: bool
    steps: list[tuple[str, StepResult]]

    @property
    def ok(self) -> bool:
        return self.applied and not self.rolled_back

    def summary(self) -> str:
        return "; ".join(f"{name}={'ok' if r.ok else 'FAIL: ' + r.message}" for name, r in self.steps)


ValidateFn = Callable[[Path], StepResult]
MultiValidateFn = Callable[[dict[str, Path]], StepResult]
ReloadFn = Callable[[], StepResult]
VerifyFn = Callable[[], StepResult]


class ConfigWriter:
    def __init__(
        self,
        target_path: str,
        validate: ValidateFn,
        reload: ReloadFn,
        verify: VerifyFn,
        backup_dir: str,
        subsystem: str,
    ):
        self.target_path = Path(target_path)
        self.validate = validate
        self.reload = reload
        self.verify = verify
        self.backup_dir = Path(backup_dir) / subsystem
        self.subsystem = subsystem

    def apply(self, new_content: str) -> ConfigTxResult:
        steps: list[tuple[str, StepResult]] = []
        self.target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.target_path.with_suffix(self.target_path.suffix + f".tmp.{os.getpid()}")
        tmp_path.write_text(new_content)

        # 1. validate
        try:
            validate_result = self.validate(tmp_path)
        except Exception as exc:  # noqa: BLE001 - convert any validator crash into a failed step
            validate_result = StepResult(False, f"validator raised: {exc}")
        steps.append(("validate", validate_result))
        if not validate_result.ok:
            tmp_path.unlink(missing_ok=True)
            return ConfigTxResult(applied=False, rolled_back=False, steps=steps)

        # 2. backup current live file (if any)
        backup_path = None
        if self.target_path.exists():
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = self.backup_dir / f"{self.target_path.name}.{int(time.time())}"
            shutil.copy2(self.target_path, backup_path)
        steps.append(("backup", StepResult(True, str(backup_path) if backup_path else "no prior file")))

        # 3. apply atomically (same filesystem rename)
        os.replace(tmp_path, self.target_path)
        steps.append(("apply", StepResult(True)))

        # 4. reload
        reload_result = self.reload()
        steps.append(("reload", reload_result))

        # 5. verify (only meaningful if reload itself succeeded)
        if reload_result.ok:
            verify_result = self.verify()
        else:
            verify_result = StepResult(False, "skipped: reload failed")
        steps.append(("verify", verify_result))

        if reload_result.ok and verify_result.ok:
            return ConfigTxResult(applied=True, rolled_back=False, steps=steps)

        # 6. rollback
        rollback_ok = True
        if backup_path is not None:
            shutil.copy2(backup_path, self.target_path)
            rb_reload = self.reload()
            rollback_ok = rb_reload.ok
            steps.append(("rollback_reload", rb_reload))
        else:
            self.target_path.unlink(missing_ok=True)
            steps.append(("rollback_remove", StepResult(True)))

        logger.error("configtx rollback for %s: %s", self.subsystem, steps)
        return ConfigTxResult(applied=True, rolled_back=True, steps=steps) if rollback_ok else ConfigTxResult(
            applied=True, rolled_back=True, steps=steps
        )


class ConfigWriterMulti:
    """Same validate -> backup -> apply -> reload -> verify -> rollback
    contract as ConfigWriter, but across several files that must land
    together as one transaction.

    Built for OLS specifically (ARCHITECTURE.md SS6/SS7): a per-account
    vhconf.conf plus the shared, fully-regenerated httpd_config.conf must
    both be in place before `openlitespeed -t` can validate either of them,
    because httpd_config.conf references vhconf.conf by path and OLS's own
    `-t` flag only ever validates the live installed config tree (confirmed
    empirically -- `-c <path>` is silently ignored). Because of that,
    "validate" here is intentionally a cheap static pre-check (e.g. balanced
    braces); the authoritative `openlitespeed -t` check runs as the first
    action inside `reload()`, gated *before* the reload command is actually
    issued -- so a real OLS validation failure is reported as a reload
    failure and triggers this class's normal rollback path, with no special
    casing needed. Reused as-is by Phase b; not needed by Phase e, where
    Postfix/Dovecot's own `check`/`-n` tools accept an arbitrary path and
    the simpler single-file ConfigWriter applies directly.
    """

    def __init__(
        self,
        targets: dict[str, str],
        validate: MultiValidateFn,
        reload: ReloadFn,
        verify: VerifyFn,
        backup_dir: str,
        subsystem: str,
    ):
        self.targets = {name: Path(path) for name, path in targets.items()}
        self.validate = validate
        self.reload = reload
        self.verify = verify
        self.backup_dir = Path(backup_dir) / subsystem
        self.subsystem = subsystem

    def apply(self, contents: dict[str, str]) -> ConfigTxResult:
        steps: list[tuple[str, StepResult]] = []
        tmp_paths: dict[str, Path] = {}
        for name, target_path in self.targets.items():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = target_path.with_suffix(target_path.suffix + f".tmp.{os.getpid()}")
            tmp_path.write_text(contents[name])
            tmp_paths[name] = tmp_path

        try:
            validate_result = self.validate(tmp_paths)
        except Exception as exc:  # noqa: BLE001
            validate_result = StepResult(False, f"validator raised: {exc}")
        steps.append(("validate", validate_result))
        if not validate_result.ok:
            for tmp_path in tmp_paths.values():
                tmp_path.unlink(missing_ok=True)
            return ConfigTxResult(applied=False, rolled_back=False, steps=steps)

        backups: dict[str, Path] = {}
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        for name, target_path in self.targets.items():
            if target_path.exists():
                backup_path = self.backup_dir / f"{name}-{target_path.name}.{int(time.time())}"
                shutil.copy2(target_path, backup_path)
                backups[name] = backup_path
        steps.append(("backup", StepResult(True, ", ".join(str(p) for p in backups.values()) or "no prior files")))

        for name, target_path in self.targets.items():
            os.replace(tmp_paths[name], target_path)
        steps.append(("apply", StepResult(True)))

        reload_result = self.reload()
        steps.append(("reload", reload_result))

        if reload_result.ok:
            verify_result = self.verify()
        else:
            verify_result = StepResult(False, "skipped: reload failed")
        steps.append(("verify", verify_result))

        if reload_result.ok and verify_result.ok:
            return ConfigTxResult(applied=True, rolled_back=False, steps=steps)

        for name, target_path in self.targets.items():
            if name in backups:
                shutil.copy2(backups[name], target_path)
            else:
                target_path.unlink(missing_ok=True)
        rb_reload = self.reload()
        steps.append(("rollback_reload", rb_reload))

        logger.error("configtx (multi) rollback for %s: %s", self.subsystem, steps)
        return ConfigTxResult(applied=True, rolled_back=True, steps=steps)
