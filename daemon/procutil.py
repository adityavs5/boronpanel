"""The only place forgehostd is allowed to shell out from.

Every call goes through run(), which always takes an argument list and never
shell=True -- a direct, structural countermeasure to CyberPanel's
CVE-2024-51567/51568 (string-concatenated shell commands). There is no
function in this module that accepts a single command string.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

logger = logging.getLogger("forgehostd.proc")


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


def run(args: list[str], *, input_text: str | None = None, timeout: float = 30.0, check: bool = False) -> ProcResult:
    if isinstance(args, str):  # pragma: no cover - defensive, should never happen
        raise TypeError("run() requires an argument list, never a shell string")
    logger.info("exec: %s", " ".join(args))
    proc = subprocess.run(
        args,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )
    result = ProcResult(args=args, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
    if check:
        result.raise_if_failed()
    return result
