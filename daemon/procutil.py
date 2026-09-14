"""The only place borond is allowed to shell out from.

Every call goes through run(), which always takes an argument list and never
shell=True -- a direct, structural countermeasure to CyberPanel's
CVE-2024-51567/51568 (string-concatenated shell commands). There is no
function in this module that accepts a single command string.
"""
from __future__ import annotations

import logging
from contextlib import ExitStack
import subprocess
from dataclasses import dataclass

logger = logging.getLogger("borond.proc")


def _redact_value(arg: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            arg = arg.replace(secret, "***REDACTED***")
    return arg


@dataclass
class ProcResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def raise_if_failed(self, context: str = "") -> "ProcResult":
        if not self.ok:
            raise RuntimeError(
                f"{context or self.args[0]} failed (rc={self.returncode}): "
                f"{self.stderr.strip() or self.stdout.strip()}"
            )
        return self


def run(
    args: list[str], *, input_text: str | None = None, timeout: float = 30.0, check: bool = False,
    redact: list[str] | None = None, cwd: str | None = None,
    input_path: str | None = None, discard_stdout: bool = False,
    uid: int | None = None, gid: int | None = None,
) -> ProcResult:
    """redact: values that must appear as literal CLI arguments (a
    third-party tool's own documented flag syntax, e.g. `--password=...`,
    that offers no stdin/config-file alternative) but must never reach the
    log line verbatim -- this project's hard "passwords never logged
    anywhere" rule (found violated once, by Joomla's own password-hashing
    call in daemon/appinstaller.py, and fixed there by switching to stdin
    instead; PrestaShop's first-party install/index_cli.php genuinely has
    no stdin-based alternative, so this is the fix for that case). Every
    other password-bearing call in this codebase pipes via input_text
    instead, which was already never logged -- redact exists only for the
    rare case where argv is the tool's only real interface."""
    if isinstance(args, str):  # pragma: no cover - defensive, should never happen
        raise TypeError("run() requires an argument list, never a shell string")
    if redact:
        logged_args = [_redact_value(a, redact) for a in args]
    else:
        logged_args = args
    logger.info("exec: %s", " ".join(logged_args))
    if input_path is not None and input_text is not None:
        raise ValueError("Choose either input_text or input_path")
    with ExitStack() as stack:
        source = stack.enter_context(open(input_path, "rb")) if input_path is not None else None
        privilege_args = {}
        if uid is not None or gid is not None:
            if uid is None or gid is None or isinstance(uid, bool) or isinstance(gid, bool) or uid <= 0 or gid <= 0:
                raise ValueError("uid and gid must both be positive integers")
            privilege_args = {"user": uid, "group": gid, "extra_groups": ()}
        proc = subprocess.run(
            args,
            input=input_text,
            stdin=source,
            stdout=subprocess.DEVNULL if discard_stdout else subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            shell=False,
            cwd=cwd,
            **privilege_args,
        )
    result = ProcResult(args=args, returncode=proc.returncode, stdout=proc.stdout or "", stderr=proc.stderr or "")
    if check:
        result.raise_if_failed()
    return result
