"""Run a command with a small Landlock filesystem policy.

The snapshot daemon uses this for root-owned restic. Restic still needs root
access to repository storage, but account file reads must stay inside approved
trees even when a tenant swaps a validated path component to a symlink.
"""
from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import re
import subprocess
import sys


SYS_LANDLOCK_CREATE_RULESET = 444
SYS_LANDLOCK_ADD_RULE = 445
SYS_LANDLOCK_RESTRICT_SELF = 446
LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
LANDLOCK_RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38

EXECUTE = 1 << 0
WRITE_FILE = 1 << 1
READ_FILE = 1 << 2
READ_DIR = 1 << 3
REMOVE_DIR = 1 << 4
REMOVE_FILE = 1 << 5
MAKE_CHAR = 1 << 6
MAKE_DIR = 1 << 7
MAKE_REG = 1 << 8
MAKE_SOCK = 1 << 9
MAKE_FIFO = 1 << 10
MAKE_BLOCK = 1 << 11
MAKE_SYM = 1 << 12
REFER = 1 << 13
TRUNCATE = 1 << 14

HANDLED_ACCESS = (
    EXECUTE | WRITE_FILE | READ_FILE | READ_DIR | REMOVE_DIR | REMOVE_FILE |
    MAKE_CHAR | MAKE_DIR | MAKE_REG | MAKE_SOCK | MAKE_FIFO | MAKE_BLOCK |
    MAKE_SYM | REFER | TRUNCATE
)
READ_TREE = READ_FILE | READ_DIR
WRITE_TREE = (
    READ_FILE | READ_DIR | WRITE_FILE | REMOVE_DIR | REMOVE_FILE | MAKE_CHAR |
    MAKE_DIR | MAKE_REG | MAKE_SOCK | MAKE_FIFO | MAKE_BLOCK | MAKE_SYM |
    REFER | TRUNCATE
)
EXEC_FILE = EXECUTE | READ_FILE
FILE_ONLY_MASK = READ_FILE | WRITE_FILE | EXECUTE | TRUNCATE


class RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class PathBeneathAttr(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int)]


def _syscall(library, number: int, *args) -> int:
    result = library.syscall(number, *args)
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result


def _abi(library) -> int:
    result = library.syscall(SYS_LANDLOCK_CREATE_RULESET, 0, 0, LANDLOCK_CREATE_RULESET_VERSION)
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return int(result)


def _ldd_paths(executable: str) -> set[str]:
    try:
        result = subprocess.run(["/usr/bin/ldd", executable], capture_output=True, text=True, timeout=5)
    except Exception:
        return set()
    paths: set[str] = set()
    for line in result.stdout.splitlines():
        match = re.search(r"=>\s+(/\S+)", line) or re.search(r"^\s*(/\S+)", line)
        if match:
            candidate = match.group(1)
            if Path(candidate).exists():
                paths.add(str(Path(candidate).resolve()))
    return paths


def _existing(paths: list[str]) -> list[str]:
    return [str(Path(path).resolve()) for path in paths if Path(path).exists()]


def _add_rule(library, ruleset_fd: int, fds: list[int], path: str, rights: int) -> None:
    real = Path(path).resolve(strict=True)
    mode = real.stat().st_mode
    if not real.is_dir():
        rights &= FILE_ONLY_MASK
    fd = os.open(real, os.O_PATH | os.O_CLOEXEC)
    fds.append(fd)
    rule = PathBeneathAttr(ctypes.c_uint64(rights), fd)
    _syscall(library, SYS_LANDLOCK_ADD_RULE, ruleset_fd, LANDLOCK_RULE_PATH_BENEATH, ctypes.byref(rule), 0)


def _restrict(read_roots: list[str], write_roots: list[str], exec_files: list[str], read_write_files: list[str]) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    if _abi(library) < 1:
        raise RuntimeError("Landlock is not available on this kernel")
    attr = RulesetAttr(ctypes.c_uint64(HANDLED_ACCESS))
    ruleset_fd = _syscall(library, SYS_LANDLOCK_CREATE_RULESET, ctypes.byref(attr), ctypes.sizeof(attr), 0)
    fds: list[int] = []
    try:
        for path in sorted(set(read_roots)):
            _add_rule(library, ruleset_fd, fds, path, READ_TREE)
        for path in sorted(set(write_roots)):
            _add_rule(library, ruleset_fd, fds, path, WRITE_TREE)
        for path in sorted(set(read_write_files)):
            _add_rule(library, ruleset_fd, fds, path, READ_FILE | WRITE_FILE)
        for path in sorted(set(exec_files)):
            _add_rule(library, ruleset_fd, fds, path, EXEC_FILE)
        if library.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        _syscall(library, SYS_LANDLOCK_RESTRICT_SELF, ruleset_fd, 0)
    finally:
        os.close(ruleset_fd)
        for fd in fds:
            os.close(fd)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a command under a Landlock filesystem policy")
    parser.add_argument("--ro", action="append", default=[], help="read-only file or directory tree")
    parser.add_argument("--rw", action="append", default=[], help="read-write directory tree")
    parser.add_argument("--exec", dest="exec_files", action="append", default=[], help="trusted executable file")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("missing command")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    exec_files = set(_existing(args.exec_files))
    for executable in list(exec_files):
        exec_files.update(_ldd_paths(executable))
    exec_files.update(_existing([
        "/etc/ld.so.cache",
        "/etc/nsswitch.conf",
        "/etc/resolv.conf",
        "/etc/hosts",
        "/etc/passwd",
        "/etc/group",
        "/etc/localtime",
        "/dev/urandom",
    ]))
    read_write_files = _existing(["/dev/null"])
    _restrict(_existing(args.ro), _existing(args.rw), sorted(exec_files), read_write_files)
    os.execvp(args.command[0], args.command)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
