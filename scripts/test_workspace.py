#!/usr/bin/env python3
"""Run tests in a bounded, dedicated temporary workspace.

Defaults are deliberately independent from /tmp so a normal pytest run cannot
silently grow /tmp/pytest-of-root on a panel host. A lightweight supervisor
stops the child if its run directory exceeds the configured cap or filesystem
free space reaches the protected floor. A separate filesystem remains the
strongest boundary and is mandatory for full runs on an installed panel host
unless the operator explicitly acknowledges the shared-filesystem risk.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid

GIB = 1024 ** 3
DEFAULT_BASE = "/srv/bt"
DEFAULT_CAP_GB = 8
DEFAULT_MIN_FREE_GB = 5
DEFAULT_KEEP_RUNS = 3
MAX_TEMP_PATH_BYTES = 40
WORKSPACE_MARKER = ".boron-test-workspace"


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"ERROR: {name} must be an integer (got {raw!r})") from exc
    if value < 1:
        raise SystemExit(f"ERROR: {name} must be at least 1")
    return value


def _safe_base() -> Path:
    base = Path(os.environ.get("BORON_TEST_TMPDIR", DEFAULT_BASE)).expanduser()
    if not base.is_absolute():
        raise SystemExit("ERROR: BORON_TEST_TMPDIR must be an absolute path")
    base.mkdir(parents=True, exist_ok=True, mode=0o755)
    metadata = base.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise SystemExit(f"ERROR: test temp base must be a real directory, not a symlink: {base}")
    if metadata.st_uid != os.geteuid():
        raise SystemExit(f"ERROR: test temp base must be owned by uid {os.geteuid()}: {base}")
    marker = base / WORKSPACE_MARKER
    existing = [item for item in base.iterdir() if item.name != WORKSPACE_MARKER]
    if existing and not marker.is_file():
        raise SystemExit(
            f"ERROR: {base} is not marked as a Boron test-only directory; "
            "refusing to use or chmod a non-empty path"
        )
    base.chmod(0o755)
    if not marker.exists():
        marker.write_text(
            "Boron test data only. Contents may be removed by the test harness.\n",
            encoding="utf-8",
        )
    marker.chmod(0o644)
    return base.resolve()


def _panel_devices() -> dict[str, int]:
    result: dict[str, int] = {}
    for raw in ("/var/lib/boron", "/var/log/boron"):
        path = Path(raw)
        if path.exists():
            result[raw] = path.stat().st_dev
    return result


def _run_directories(base: Path) -> list[Path]:
    return sorted(
        (
            item for item in base.iterdir()
            if item.is_dir() and not item.is_symlink()
            and item.name.startswith(("r-", "run-"))
        ),
        key=lambda item: (item.stat().st_mtime_ns, item.name),
        reverse=True,
    )


def _prune(base: Path, keep: int, *, exclude: Path | None = None) -> None:
    retained = 0
    for item in _run_directories(base):
        if exclude is not None and item == exclude:
            retained += 1
            continue
        if retained < keep:
            retained += 1
            continue
        shutil.rmtree(item)


def _directory_bytes(path: Path) -> int:
    total = 0
    for root, directories, files in os.walk(path, followlinks=False):
        # Never traverse a symlink planted by a test.
        directories[:] = [name for name in directories if not (Path(root) / name).is_symlink()]
        for name in files:
            try:
                total += (Path(root) / name).lstat().st_size
            except FileNotFoundError:
                pass
    return total


def _terminate_group(process: subprocess.Popen, message: str) -> int:
    print(message, file=sys.stderr, flush=True)
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return process.wait()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
    return 75


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-separate", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")

    base = _safe_base()
    cap_gb = _positive_int("BORON_TEST_TMP_MAX_GB", DEFAULT_CAP_GB)
    min_free_gb = _positive_int("BORON_TEST_MIN_FREE_GB", DEFAULT_MIN_FREE_GB)
    keep = _positive_int("BORON_TEST_KEEP_RUNS", DEFAULT_KEEP_RUNS)
    free = shutil.disk_usage(base).free
    if free < min_free_gb * GIB:
        raise SystemExit(
            f"ERROR: test filesystem for {base} has {free / GIB:.1f} GiB free; "
            f"at least {min_free_gb} GiB is required"
        )

    panel_devices = _panel_devices()
    shared = [path for path, device in panel_devices.items() if device == base.stat().st_dev]
    acknowledged = os.environ.get("BORON_TEST_ALLOW_SHARED_FS") == "1"
    if shared:
        detail = ", ".join(shared)
        if args.require_separate and not acknowledged:
            raise SystemExit(
                f"ERROR: full tests would share the {base} filesystem with {detail}. "
                "Set BORON_TEST_TMPDIR to a separate filesystem. "
                "BORON_TEST_ALLOW_SHARED_FS=1 is an explicit last-resort override."
            )
        print(
            f"WARNING: {base} shares a filesystem with {detail}; the size/free-space guard remains active",
            file=sys.stderr,
        )

    # Leave room for the new run while preserving the requested last-three policy.
    _prune(base, max(0, keep - 1))
    for _attempt in range(10):
        run_dir = base / f"r-{uuid.uuid4().hex[:8]}"
        try:
            run_dir.mkdir(mode=0o755)
        except FileExistsError:
            continue
        break
    else:
        raise SystemExit(f"ERROR: could not allocate a unique test run below {base}")
    temp_dir = run_dir / "t"
    temp_path_bytes = len(os.fsencode(str(temp_dir)))
    if temp_path_bytes > MAX_TEMP_PATH_BYTES:
        shutil.rmtree(run_dir)
        raise SystemExit(
            f"ERROR: test workspace temp path is {temp_path_bytes} bytes; maximum is "
            f"{MAX_TEMP_PATH_BYTES} to leave room for Unix socket names. "
            "Set BORON_TEST_TMPDIR to a shorter dedicated path such as /srv/bt."
        )
    temp_dir.mkdir(mode=0o755)
    default_report_dir = run_dir / "a"
    report_dir = Path(os.environ.get("BORON_TEST_REPORT_DIR", str(default_report_dir)))
    report_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if report_dir == default_report_dir:
        report_dir.chmod(0o700)

    env = os.environ.copy()
    env.update({
        "TMPDIR": str(temp_dir),
        "TMP": str(temp_dir),
        "TEMP": str(temp_dir),
        "PYTEST_DEBUG_TEMPROOT": str(temp_dir),
        "PYTHONPYCACHEPREFIX": str(run_dir / "p"),
        "BORON_TEST_RUN_DIR": str(run_dir),
        "BORON_TEST_REPORT_DIR": str(report_dir),
    })
    existing_addopts = env.get("PYTEST_ADDOPTS", "").strip()
    env["PYTEST_ADDOPTS"] = (
        f"{existing_addopts} --basetemp={run_dir / 'pyt'} -rs"
    ).strip()

    print(
        f"Boron test workspace: {run_dir} (cap {cap_gb} GiB, protected free floor {min_free_gb} GiB, keep {keep})",
        flush=True,
    )
    process = subprocess.Popen(command, env=env, start_new_session=True)
    status: int | None = None
    while status is None:
        status = process.poll()
        if status is not None:
            break
        used = _directory_bytes(run_dir)
        free_now = shutil.disk_usage(base).free
        if used > cap_gb * GIB:
            status = _terminate_group(
                process,
                f"ERROR: test run exceeded BORON_TEST_TMP_MAX_GB={cap_gb} GiB at {run_dir}",
            )
            break
        if free_now < min_free_gb * GIB:
            status = _terminate_group(
                process,
                f"ERROR: test filesystem free space fell below BORON_TEST_MIN_FREE_GB={min_free_gb} GiB",
            )
            break
        time.sleep(2)
    _prune(base, keep, exclude=run_dir)
    print(f"Boron test reports: {report_dir}", flush=True)
    return int(status)


if __name__ == "__main__":
    raise SystemExit(main())
