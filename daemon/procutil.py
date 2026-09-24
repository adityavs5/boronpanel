"""The only place borond is allowed to shell out from.

Every call goes through run(), which always takes an argument list and never
shell=True -- a direct, structural countermeasure to CyberPanel's
CVE-2024-51567/51568 (string-concatenated shell commands). There is no
function in this module that accepts a single command string.
"""
from __future__ import annotations

import logging
import os
import selectors
import signal
import time
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



MAX_TENANT_OUTPUT = 4 * 1024 * 1024


def _bounded_run(args, *, input_text, source, discard_stdout, timeout, cwd, privileges, limit):
    """Drain both pipes incrementally; never buffer unlimited tenant output.

    A dedicated process group permits timeout/overflow cleanup of children
    holding pipes open. Input is also pumped incrementally to avoid deadlock
    when a command writes output before consuming its complete input.
    """
    deadline = time.monotonic() + timeout
    output = [bytearray(), bytearray()]
    payload = memoryview(input_text.encode() if input_text is not None else b'')
    with subprocess.Popen(
        args, stdin=subprocess.PIPE if input_text is not None else source,
        stdout=subprocess.DEVNULL if discard_stdout else subprocess.PIPE,
        stderr=subprocess.PIPE, shell=False, cwd=cwd,
        start_new_session=True, **privileges,
    ) as proc:
        try:
            with selectors.DefaultSelector() as selector:
                for index, stream in enumerate((proc.stdout, proc.stderr)):
                    if stream is not None:
                        os.set_blocking(stream.fileno(), False)
                        selector.register(stream, selectors.EVENT_READ, index)
                if proc.stdin is not None:
                    if payload:
                        os.set_blocking(proc.stdin.fileno(), False)
                        selector.register(proc.stdin, selectors.EVENT_WRITE, 2)
                    else:
                        proc.stdin.close()
                captured = 0
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(args, timeout)
                    for key, _events in selector.select(remaining):
                        if key.data == 2:
                            try:
                                written = os.write(key.fd, payload[:65536])
                                payload = payload[written:]
                            except BrokenPipeError:
                                payload = payload[:0]
                            if not payload:
                                selector.unregister(key.fileobj)
                                key.fileobj.close()
                        else:
                            chunk = os.read(key.fd, 65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                key.fileobj.close()
                                continue
                            captured += len(chunk)
                            if captured > limit:
                                raise RuntimeError('Command output exceeded the capture limit')
                            output[key.data].extend(chunk)
                proc.wait(timeout=max(0.001, deadline - time.monotonic()))
        except BaseException:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            raise
        return subprocess.CompletedProcess(args, proc.returncode,
            output[0].decode(errors='replace'), output[1].decode(errors='replace'))

def run(
    args: list[str], *, input_text: str | None = None, timeout: float = 30.0, check: bool = False,
    redact: list[str] | None = None, cwd: str | None = None,
    input_path: str | None = None, discard_stdout: bool = False,
    uid: int | None = None, gid: int | None = None,
    output_limit: int | None = None,
) -> ProcResult:
    """Run an argv command. redact masks known secrets in the logged argv,
    but cannot hide them from process listings; prefer input_text for secrets.
    """
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
        # Tenant package hooks and CLI commands can print arbitrary data.
        # Their output must be bounded before collection, not sliced afterward.
        if output_limit is None and (uid is not None or os.path.basename(args[0]) == 'runuser'):
            output_limit = MAX_TENANT_OUTPUT
        if output_limit is not None:
            if output_limit <= 0:
                raise ValueError('output_limit must be positive')
            proc = _bounded_run(args, input_text=input_text, source=source,
                discard_stdout=discard_stdout, timeout=timeout, cwd=cwd,
                privileges=privilege_args, limit=output_limit)
        else:
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
