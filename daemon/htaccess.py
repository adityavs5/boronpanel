"""Safe `.htaccess` reload actions and a coalescing inotify observer."""
from __future__ import annotations

import ctypes
import errno
import logging
import os
import select as io_select
import struct
import time
from pathlib import Path

from sqlalchemy import select

from daemon import ols
from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain
from shared.validation import ValidationError, validate_domain, validate_username

logger = logging.getLogger("borond.htaccess")

IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_IGNORED = 0x00008000
IN_ISDIR = 0x40000000
WATCH_MASK = IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE | IN_DELETE_SELF | IN_MOVE_SELF
EVENT = struct.Struct("iIII")


def _owned_domain(username: str, domain_name: str) -> tuple[str, str]:
    with write_session() as session:
        row = session.execute(
            select(Domain.docroot, Account.username)
            .join(Account, Domain.account_id == Account.id)
            .where(Domain.domain == domain_name, Account.username == username)
        ).first()
    if row is None:
        raise ValidationError("Domain does not belong to this account")
    docroot = os.path.realpath(row.docroot)
    home = os.path.realpath(os.path.join(settings.home_base, username))
    if docroot != home and not docroot.startswith(home + os.sep):
        raise ValidationError("Domain document root is outside its account home")
    if not os.path.isdir(docroot) or os.path.islink(row.docroot):
        raise ValidationError("Domain document root is unavailable or unsafe")
    return domain_name, docroot


def reload_domain(params: dict) -> dict:
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])
    domain_name, docroot = _owned_domain(username, domain_name)
    result = ols.graceful_reload({"confirm": True})
    return {"domain": domain_name, "docroot": docroot, "reloaded": True, "config_valid": result["config_valid"]}


class HtaccessWatcher:
    def __init__(self) -> None:
        self.fd = -1
        self.watches: dict[int, tuple[str, str, str]] = {}
        self.paths: set[str] = set()
        self.pending: dict[str, float] = {}
        self.last_inventory = 0.0
        self.libc = ctypes.CDLL(None, use_errno=True)

    def _start(self) -> None:
        if self.fd >= 0:
            return
        fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1 failed")
        self.fd = fd

    def _add_dir(self, directory: str, domain: str, root: str) -> None:
        if directory in self.paths or os.path.islink(directory):
            return
        real = os.path.realpath(directory)
        if real != root and not real.startswith(root + os.sep):
            return
        wd = self.libc.inotify_add_watch(self.fd, os.fsencode(directory), WATCH_MASK)
        if wd < 0:
            error = ctypes.get_errno()
            if error not in {errno.EACCES, errno.ENOENT, errno.ENOSPC}:
                logger.warning("Could not watch %s: %s", directory, os.strerror(error))
            return
        self.watches[wd] = (domain, root, directory)
        self.paths.add(directory)

    def _add_tree(self, root: str, domain: str) -> None:
        for directory, names, _files in os.walk(root, followlinks=False):
            names[:] = [name for name in names if not os.path.islink(os.path.join(directory, name))]
            self._add_dir(directory, domain, root)

    def refresh_inventory(self) -> None:
        self._start()
        with write_session() as session:
            rows = session.execute(select(Domain.domain, Domain.docroot)).all()
        inventory = {
            (domain, os.path.realpath(value))
            for domain, value in rows
            if os.path.isdir(os.path.realpath(value)) and not os.path.islink(value)
        }
        # Domain/docroot removals must also remove their recursive watches;
        # otherwise a path later reused outside Boron remains an input to the
        # global reload loop.
        for wd, (domain, root, directory) in list(self.watches.items()):
            if (domain, root) in inventory and os.path.isdir(directory):
                continue
            self.libc.inotify_rm_watch(self.fd, wd)
            self.watches.pop(wd, None)
            self.paths.discard(directory)
        live_domains = {domain for domain, _root in inventory}
        for domain in set(self.pending) - live_domains:
            self.pending.pop(domain, None)
        for domain, root in inventory:
            self._add_tree(root, domain)
        self.last_inventory = time.monotonic()

    def poll(self) -> dict:
        if self.fd < 0 or time.monotonic() - self.last_inventory > 60:
            self.refresh_inventory()
        changed = set()
        while io_select.select([self.fd], [], [], 0)[0]:
            try:
                payload = os.read(self.fd, 256 * 1024)
            except BlockingIOError:
                break
            offset = 0
            while offset + EVENT.size <= len(payload):
                wd, mask, _cookie, length = EVENT.unpack_from(payload, offset)
                offset += EVENT.size
                raw_name = payload[offset:offset + length]
                offset += length
                name = raw_name.rstrip(b"\0").decode(errors="replace")
                meta = self.watches.get(wd)
                if meta is None:
                    continue
                domain, root, directory = meta
                if mask & (IN_DELETE_SELF | IN_MOVE_SELF | IN_IGNORED):
                    self.watches.pop(wd, None)
                    self.paths.discard(directory)
                path = os.path.join(directory, name) if name else directory
                if mask & IN_ISDIR and mask & (IN_CREATE | IN_MOVED_TO) and os.path.isdir(path):
                    self._add_tree(path, domain)
                if name == ".htaccess":
                    changed.add(domain)
        now = time.monotonic()
        for domain in changed:
            self.pending[domain] = now + max(1, settings.htaccess_reload_debounce_seconds)
        due = [domain for domain, deadline in self.pending.items() if deadline <= now]
        reloaded: list[str] = []
        if due:
            # One validated graceful reload covers all coalesced vhosts.
            try:
                ols.graceful_reload({"confirm": True})
                logger.info("Reloaded OpenLiteSpeed after .htaccess changes: %s", ", ".join(sorted(due)))
                reloaded = sorted(due)
                for domain in due:
                    self.pending.pop(domain, None)
            except Exception:
                logger.exception("OpenLiteSpeed reload after .htaccess change failed")
                for domain in due:
                    self.pending[domain] = now + 30
        return {"changed": sorted(changed), "reloaded": reloaded}


watcher = HtaccessWatcher()
