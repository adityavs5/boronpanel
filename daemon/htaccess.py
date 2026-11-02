"""Safe `.htaccess` reload actions and a coalescing inotify observer."""
from __future__ import annotations

import ctypes
import errno
import ipaddress
import logging
import os
import re
import stat
import select as io_select
import struct
import time
from pathlib import Path

from sqlalchemy import select

from daemon import ols, safeio
from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain
from shared.validation import PROTECTED_DIR_RE, ValidationError, validate_domain, validate_username

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
    """Apply .htaccess edits now. Access directives are translated into the
    generated vhost, so the account's vhosts are re-rendered (validated and
    reloaded through the normal transaction) rather than only reloaded."""
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])
    domain_name, docroot = _owned_domain(username, domain_name)
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        session.expunge(account)
    ols.refresh_vhost(account)
    rules = scan_access_rules(docroot)
    watcher.access_signatures[domain_name] = _access_signature(rules)
    return {"domain": domain_name, "docroot": docroot, "reloaded": True, "config_valid": True,
            **_public_access_report(rules)}


def access_status(params: dict) -> dict:
    """Read-only report of how this domain's .htaccess access directives are
    enforced: which directories OpenLiteSpeed now protects natively, and
    which directives it cannot honour (so the customer is never left
    believing a directory is protected when it is not)."""
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])
    domain_name, docroot = _owned_domain(username, domain_name)
    return {"domain": domain_name, **_public_access_report(scan_access_rules(docroot))}


# --- Apache access-control directives ---------------------------------------
#
# OpenLiteSpeed reads only rewrite directives from .htaccess. Apache access
# control (Require / Order / Allow / Deny) is silently ignored, which used to
# expose directories a cPanel/Apache migrant had protected. The common forms
# are translated here into native per-directory OLS contexts (allowBrowse 0
# for deny-all, accessControl for IP allow/deny lists) by daemon/ols.py; every
# other access directive is reported as unsupported rather than ignored.
# Boron's own protections never rely on these directives.

ACCESS_SCAN_MAX_DIRECTORIES = 20000
ACCESS_SCAN_MAX_SECONDS = 2.0
ACCESS_SCAN_MAX_FILES = 512
HTACCESS_MAX_BYTES = 256 * 1024
MAX_ACCESS_ADDRESSES = 64
MAX_REPORTED_ISSUES = 100
_SKIPPED_TREES = frozenset({".git", "node_modules"})

# Modules a stock Apache 2.4 build has; <IfModule> blocks are evaluated as
# Apache 2.4 would, so the "<IfModule !mod_authz_core.c>" legacy branch of
# the usual dual-syntax snippet is skipped. Unknown modules are assumed
# present (applying their access directives errs towards denying).
_PRESENT_MODULES = frozenset({
    "mod_authz_core", "mod_authz_host", "mod_access_compat", "mod_rewrite", "mod_headers",
    "mod_mime", "mod_expires", "mod_deflate", "mod_setenvif", "mod_alias", "mod_dir",
    "mod_version", "mod_auth_basic", "mod_authn_file", "mod_authz_user", "mod_env",
})
_TRANSPARENT_BLOCKS = frozenset({"ifmodule", "ifdefine", "ifversion", "ifdirective", "ifsection", "iffile"})
_AUTHZ_BLOCKS = frozenset({"requireall", "requireany", "requirenone"})
_ACCESS_DIRECTIVES = frozenset({"require", "order", "allow", "deny", "satisfy"})
_BLOCK_RE = re.compile(r"\A<\s*(/?)\s*([A-Za-z]+)(.*)>\s*\Z")
_HOST_RE = re.compile(r"\A[0-9a-fA-F:.]+(/[0-9a-fA-F:.]+)?\Z")


def _module_present(argument: str) -> bool:
    name = argument.strip().strip('"').lower()
    negated = name.startswith("!")
    name = name.lstrip("!").strip()
    if name.endswith(".c"):
        name = name[:-2]
    elif name.endswith("_module"):
        name = "mod_" + name[:-len("_module")]
    # Unknown modules count as present for <IfModule x> and absent for
    # <IfModule !x>: either way their access directives are applied.
    return not (negated and name in _PRESENT_MODULES)


def _address(value: str) -> str | None:
    """An Apache host spec as an OLS access-control entry, or None when it
    cannot be expressed (host names, env= forms)."""
    value = value.strip()
    if not value or not _HOST_RE.match(value):
        return None
    try:
        if "/" in value:
            return str(ipaddress.ip_network(value, strict=False))
        if ":" not in value and value.count(".") < 3:
            # Apache partial addresses: "10.1" means 10.1.0.0/16.
            octets = [part for part in value.split(".") if part != ""]
            if not 1 <= len(octets) <= 3:
                return None
            network = ".".join(octets + ["0"] * (4 - len(octets)))
            return str(ipaddress.ip_network(f"{network}/{8 * len(octets)}", strict=False))
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def _logical_lines(text: str):
    pending = ""
    start = 0
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not pending:
            start = number
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        yield start, (pending + line).strip()
        pending = ""
    if pending:
        yield start, pending.strip()


def parse_access_directives(text: str) -> tuple[dict | None, list[dict]]:
    """Return (rule, unsupported) for one .htaccess file.

    rule is None (no access directives), {"mode": "deny"},
    {"mode": "allow"} or {"mode": "ip", "allow": [...], "deny": [...]}
    where "*" means every address. unsupported lists directives that
    cannot be enforced, each {"line", "directive"}.
    """
    unsupported: list[dict] = []
    stack: list[tuple[str, bool]] = []
    requires: list[tuple[str, list[str], int, str]] = []
    order = None
    allow_from: list[str] = []
    deny_from: list[str] = []
    seen_legacy = False

    def flag(number: int, directive: str) -> None:
        unsupported.append({"line": number, "directive": directive[:200]})

    for number, line in _logical_lines(text):
        if not line or line.startswith("#"):
            continue
        block = _BLOCK_RE.match(line)
        if block:
            closing, name, argument = block.group(1), block.group(2).lower(), block.group(3)
            if closing:
                if stack and stack[-1][0] == name:
                    stack.pop()
                continue
            active = (not stack or stack[-1][1]) and (name != "ifmodule" or _module_present(argument))
            stack.append((name, active))
            continue
        if stack and not stack[-1][1]:
            continue
        words = line.split()
        directive = words[0].lower()
        if directive not in _ACCESS_DIRECTIVES:
            continue
        scopes = [name for name, _active in stack if name not in _TRANSPARENT_BLOCKS]
        authz = [name for name in scopes if name in _AUTHZ_BLOCKS]
        if any(name not in _AUTHZ_BLOCKS for name in scopes):
            # <Files>, <FilesMatch>, <Limit>, <If>, ...: per-file or
            # per-method scope has no per-directory OLS equivalent.
            flag(number, line)
            continue
        arguments = words[1:]
        if directive == "require":
            if not arguments:
                flag(number, line)
                continue
            requires.append((authz[-1] if authz else "requireany", arguments, number, line))
        elif directive == "order":
            seen_legacy = True
            value = "".join(arguments).lower()
            if value not in ("deny,allow", "allow,deny"):
                flag(number, line)
                continue
            order = value
        elif directive in ("allow", "deny"):
            seen_legacy = True
            if len(arguments) < 2 or arguments[0].lower() != "from":
                flag(number, line)
                continue
            target = allow_from if directive == "allow" else deny_from
            for value in arguments[1:]:
                if value.lower() == "all":
                    target.append("*")
                    continue
                address = _address(value)
                if address is None:
                    flag(number, line)
                    continue
                target.append(address)
        elif directive == "satisfy":
            if "".join(arguments).lower() != "all":
                flag(number, line)

    modern = _require_rule(requires, unsupported)
    legacy = _legacy_rule(order, allow_from, deny_from) if seen_legacy else None
    rule = _stricter(modern, legacy)
    if rule and rule["mode"] == "ip":
        for key in ("allow", "deny"):
            values = list(dict.fromkeys(rule[key]))
            if len(values) > MAX_ACCESS_ADDRESSES:
                # Too many entries to render safely; fail closed.
                unsupported.append({"line": 0, "directive": f"more than {MAX_ACCESS_ADDRESSES} addresses"})
                return {"mode": "deny"}, unsupported
            rule[key] = values
    return rule, unsupported


def _require_rule(requires, unsupported) -> dict | None:
    if not requires:
        return None
    granted_all = False
    denied_all = False
    allow: list[str] = []
    deny: list[str] = []
    authentication = False
    for scope, arguments, number, line in requires:
        kind = arguments[0].lower()
        negated = kind == "not"
        if negated:
            arguments = arguments[1:]
            kind = arguments[0].lower() if arguments else ""
        if kind == "all" and len(arguments) == 2 and arguments[1].lower() in ("granted", "denied") and not negated:
            if arguments[1].lower() == "granted":
                granted_all = True
            else:
                denied_all = True
            continue
        if kind == "ip" and len(arguments) > 1 and scope != "requirenone":
            addresses = [_address(value) for value in arguments[1:]]
            if None in addresses:
                unsupported.append({"line": number, "directive": line[:200]})
                continue
            if negated:
                if scope != "requireall":
                    unsupported.append({"line": number, "directive": line[:200]})
                    continue
                deny.extend(addresses)
            else:
                allow.extend(addresses)
            continue
        # valid-user, user, group, host, env, expr, method, ...
        authentication = authentication or kind in ("valid-user", "user", "group")
        unsupported.append({"line": number, "directive": line[:200]})
    if deny:
        # <RequireAll> Require all granted + Require not ip ...: everyone
        # except the listed addresses.
        if not (granted_all or allow):
            return {"mode": "deny"}
        return {"mode": "ip", "allow": allow or ["*"], "deny": deny}
    if granted_all:
        return {"mode": "allow"}
    if allow:
        return {"mode": "ip", "allow": allow, "deny": ["*"]}
    if denied_all:
        return {"mode": "deny"}
    # Only unsupported providers (e.g. valid-user): Apache would refuse
    # anonymous visitors, so fail closed instead of serving the content.
    # "authentication" lets a panel-managed password realm on the same
    # directory (daemon/fileauth.py) take precedence over the deny.
    return {"mode": "deny", "authentication": authentication}


def _legacy_rule(order, allow_from, deny_from) -> dict:
    allow_ips = [value for value in allow_from if value != "*"]
    deny_ips = [value for value in deny_from if value != "*"]
    allow_all = "*" in allow_from
    deny_all = "*" in deny_from
    if (order or "deny,allow") == "deny,allow":
        # Default allow; Allow overrides Deny.
        if deny_all:
            if allow_all:
                return {"mode": "allow"}
            return {"mode": "ip", "allow": allow_ips, "deny": ["*"]} if allow_ips else {"mode": "deny"}
        return {"mode": "ip", "allow": ["*"], "deny": deny_ips} if deny_ips else {"mode": "allow"}
    # allow,deny: default deny; Deny overrides Allow.
    if deny_all or not (allow_all or allow_ips):
        return {"mode": "deny"}
    if allow_all:
        return {"mode": "ip", "allow": ["*"], "deny": deny_ips} if deny_ips else {"mode": "allow"}
    return {"mode": "ip", "allow": allow_ips, "deny": ["*", *deny_ips]}


_STRICTNESS = {"allow": 0, "ip": 1, "deny": 2}


def _stricter(first: dict | None, second: dict | None) -> dict | None:
    if first is None or second is None:
        return first or second
    return first if _STRICTNESS[first["mode"]] >= _STRICTNESS[second["mode"]] else second


def _read_htaccess(directory_fd: int) -> str | None:
    try:
        fd = os.open(".htaccess", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory_fd)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None
        data = os.read(fd, HTACCESS_MAX_BYTES + 1)
    finally:
        os.close(fd)
    return data[:HTACCESS_MAX_BYTES].decode("utf-8", errors="replace")


def scan_access_rules(docroot: str) -> dict:
    """Walk one document root (bounded, never following symlinks) and parse
    every .htaccess. Returns {"rules": {relative_dir: rule}, "unsupported":
    [{"path", "line", "directive"}], "complete": bool}; relative_dir is ""
    for the document root itself."""
    rules: dict[str, dict] = {}
    unsupported: list[dict] = []
    complete = True
    try:
        root_fd = safeio.open_dir_beneath(docroot)
    except (OSError, safeio.UnsafePathError):
        return {"rules": rules, "unsupported": unsupported, "complete": True}
    deadline = time.monotonic() + ACCESS_SCAN_MAX_SECONDS
    visited = 0
    files = 0
    pending: list[tuple[int, str]] = [(root_fd, "")]
    try:
        while pending:
            directory_fd, relative = pending.pop()
            try:
                visited += 1
                if visited > ACCESS_SCAN_MAX_DIRECTORIES or time.monotonic() > deadline:
                    complete = False
                    continue
                text = _read_htaccess(directory_fd)
                if text is not None:
                    files += 1
                    if files > ACCESS_SCAN_MAX_FILES:
                        complete = False
                        continue
                    rule, issues = parse_access_directives(text)
                    path = f"{relative}/.htaccess" if relative else ".htaccess"
                    if rule is not None and relative and not PROTECTED_DIR_RE.match(relative):
                        # Not expressible as an OLS context path.
                        issues.append({"line": 0, "directive": "directory name prevents OpenLiteSpeed "
                                       "from enforcing these rules; rename it using letters, digits, '.', '_' or '-'"})
                        rule = None
                    for issue in issues:
                        if len(unsupported) < MAX_REPORTED_ISSUES:
                            unsupported.append({"path": path, **issue})
                    if rule is not None:
                        rules[relative] = rule
                try:
                    with os.scandir(directory_fd) as entries:
                        names = [entry.name for entry in entries
                                 if entry.is_dir(follow_symlinks=False) and entry.name not in _SKIPPED_TREES]
                except OSError:
                    continue
                for name in sorted(names, reverse=True):
                    try:
                        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                        dir_fd=directory_fd)
                    except OSError:
                        continue
                    pending.append((child, f"{relative}/{name}" if relative else name))
            finally:
                os.close(directory_fd)
    finally:
        for directory_fd, _relative in pending:
            os.close(directory_fd)
    return {"rules": rules, "unsupported": unsupported, "complete": complete}


def effective_access_contexts(scan: dict) -> list[dict]:
    """Directories needing their own OLS context, outermost first. A
    directory whose own rule is "allow" only needs a context when an
    ancestor restricts access (a child's directives replace the parent's,
    as with Apache's default AuthMerging Off)."""
    rules = scan["rules"]
    contexts = []
    for relative in sorted(rules, key=lambda value: (value.count("/"), value)):
        rule = rules[relative]
        restricted_ancestor = False
        parts = relative.split("/") if relative else []
        for depth in range(len(parts) - 1, -1, -1):
            ancestor = "/".join(parts[:depth])
            if ancestor in rules:
                restricted_ancestor = rules[ancestor]["mode"] != "allow"
                break
        if rule["mode"] == "allow" and not restricted_ancestor:
            continue
        contexts.append({"relative_path": relative, **rule})
    return contexts


def _access_signature(scan: dict) -> tuple:
    return tuple(
        (context["relative_path"], context["mode"], tuple(context.get("allow", ())), tuple(context.get("deny", ())))
        for context in effective_access_contexts(scan)
    )


def _public_access_report(scan: dict) -> dict:
    return {
        "access_rules": [
            {"directory": "/" + context["relative_path"], "mode": context["mode"],
             "allow": context.get("allow", []), "deny": context.get("deny", [])}
            for context in effective_access_contexts(scan)
        ],
        "unsupported_access_directives": scan["unsupported"],
        "access_scan_complete": scan["complete"],
    }


class HtaccessWatcher:
    def __init__(self) -> None:
        self.fd = -1
        self.watches: dict[int, tuple[str, str, str]] = {}
        self.paths: set[str] = set()
        self.pending: dict[str, float] = {}
        self.access_signatures: dict[str, tuple] = {}
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
        # Resolve configuration before draining inotify: os.read() consumes
        # the events, so nothing after it may fail before they are recorded.
        debounce = max(1.0, float(settings.htaccess_watch_debounce_seconds))
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
            self.pending[domain] = now + debounce
        due = [domain for domain, deadline in self.pending.items() if deadline <= now]
        reloaded: list[str] = []
        if due:
            try:
                self._apply(due)
                logger.info("Reloaded OpenLiteSpeed after .htaccess changes: %s", ", ".join(sorted(due)))
                reloaded = sorted(due)
                for domain in due:
                    self.pending.pop(domain, None)
            except Exception:
                logger.exception("OpenLiteSpeed reload after .htaccess change failed")
                for domain in due:
                    self.pending[domain] = now + 30
        return {"changed": sorted(changed), "reloaded": reloaded}

    def _apply(self, domains: list[str]) -> None:
        """Rewrite rules only need a reload, but translated access rules live
        in the generated vhost, so an account whose access directives
        changed gets its vhosts re-rendered (which also reloads)."""
        with write_session() as session:
            rows = session.execute(
                select(Domain.domain, Domain.docroot, Account.id)
                .join(Account, Domain.account_id == Account.id)
                .where(Domain.domain.in_(domains), Account.status.in_(("active", "suspended")))
            ).all()
        stale_accounts = set()
        new_signatures = {}
        for domain_name, docroot, account_id in rows:
            signature = _access_signature(scan_access_rules(docroot))
            if self.access_signatures.get(domain_name) != signature:
                stale_accounts.add(account_id)
                new_signatures[domain_name] = signature
        for account_id in sorted(stale_accounts):
            with write_session() as session:
                account = session.get(Account, account_id)
                if account is None:
                    continue
                session.expunge(account)
            ols.refresh_vhost(account)
        if not stale_accounts:
            # One validated graceful reload covers all coalesced vhosts.
            ols.graceful_reload({"confirm": True})
        # Recorded only once the vhosts carry them: a failed re-render is
        # retried by poll(), and must re-render again rather than only reload.
        self.access_signatures.update(new_signatures)


watcher = HtaccessWatcher()
