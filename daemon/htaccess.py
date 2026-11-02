"""Safe `.htaccess` reload actions and a coalescing inotify observer."""
from __future__ import annotations

import ctypes
import errno
import ipaddress
import json
import logging
import os
import re
import stat
import select as io_select
import struct
import threading
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


def _owned_domain(username: str, domain_name: str) -> tuple[str, str, int]:
    with write_session() as session:
        row = session.execute(
            select(Domain.docroot, Account.username, Account.id)
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
    return domain_name, docroot, row.id


def reload_domain(params: dict) -> dict:
    """Apply .htaccess edits now. Access directives are translated into the
    generated vhost, so the account's vhosts are re-rendered (validated and
    reloaded through the normal transaction) rather than only reloaded."""
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])
    domain_name, docroot, account_id = _owned_domain(username, domain_name)
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        session.expunge(account)
    ols.refresh_vhost(account)
    rules = account_access_scans(account_id, [docroot])[docroot]
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
    domain_name, docroot, account_id = _owned_domain(username, domain_name)
    return {"domain": domain_name, **_public_access_report(account_access_scans(account_id, [docroot])[docroot])}


# --- Apache access-control directives ---------------------------------------
#
# OpenLiteSpeed reads only rewrite directives from .htaccess. Apache access
# control (Require / Order / Allow / Deny) is silently ignored, which used to
# expose directories a cPanel/Apache migrant had protected. The common forms
# are translated here into native per-directory OLS contexts (allowBrowse 0
# for deny-all, accessControl for IP allow/deny lists) by daemon/ols.py; every
# other access directive is reported as unsupported rather than ignored.
# Boron's own protections never rely on these directives.
#
# Access rules inside <Files>, <FilesMatch>, <Limit> and similar sections
# apply to some files or methods only, which a directory context cannot
# express. A section that grants access ("Deny from all" plus "<FilesMatch
# \.(css|js|png)$> Allow from all") must never turn into a 403 for the files
# it allows, so such grants relax the directory's rule -- and every rule in
# its subtree, where Apache applies them too -- to the least restrictive
# combination (_union). Sections that only restrict stay reported only.

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
# Unlike <Files>, whose directives replace the directory's for matching
# files, <Limit> directives join the surrounding ones for the methods named.
_PER_METHOD_BLOCKS = frozenset({"limit", "limitexcept"})
_SECTION_NOTE = ("access rules inside <Files>, <FilesMatch> or <Limit> cannot be enforced per file or "
                 "per method; this directory's restriction is relaxed so the files they allow stay reachable")
# Reported on each directory whose own restriction such a grant relaxed (the
# grant may sit in a parent's .htaccess): the report must never let a
# customer believe a directory is protected when it is not.
_RELAXED_NOTE = ("this directory's access restriction is not enforced: a <Files>, <FilesMatch> or <Limit> "
                 "section here or in a parent folder allows some files, and OpenLiteSpeed cannot limit "
                 "that to those files")
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
    """Return (rule, unsupported) for one .htaccess file; see
    parse_access_file, which also returns the per-file/per-method grants."""
    parsed = parse_access_file(text)
    return parsed["rule"], parsed["unsupported"]


def _directives() -> dict:
    return {"requires": [], "order": None, "allow_from": [], "deny_from": [], "legacy": False}


def parse_access_file(text: str) -> dict:
    """Parse one .htaccess file into {"rule", "grants", "unsupported"}.

    rule is the directory-wide access rule: None (no access directives),
    {"mode": "deny"}, {"mode": "allow"} or {"mode": "ip", "allow": [...],
    "deny": [...]} where "*" means every address. grants are the rules of
    <Files>/<FilesMatch>/<Limit>/... sections that admit someone (mode
    "allow" or "ip"): they cannot be enforced per file or method, so
    effective_access_contexts keeps what they allow reachable. unsupported
    lists directives that cannot be enforced as written, each
    {"line", "directive"}.
    """
    unsupported: list[dict] = []
    # (block name, active, index into sections of the enclosing scoped section)
    stack: list[tuple[str, bool, int | None]] = []
    top = _directives()
    sections: list[tuple[bool, dict]] = []  # (per-method, directives)

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
            section = stack[-1][2] if stack else None
            if section is None and name not in _TRANSPARENT_BLOCKS and name not in _AUTHZ_BLOCKS:
                # <Files>, <FilesMatch>, <Limit>, <If>, ...: the directives
                # inside apply to some files or methods only.
                sections.append((name in _PER_METHOD_BLOCKS, _directives()))
                section = len(sections) - 1
            stack.append((name, active, section))
            continue
        if stack and not stack[-1][1]:
            continue
        words = line.split()
        directive = words[0].lower()
        if directive not in _ACCESS_DIRECTIVES:
            continue
        authz = [name for name, _active, _section in stack if name in _AUTHZ_BLOCKS]
        section = stack[-1][2] if stack else None
        if section is not None:
            # No per-file or per-method OLS equivalent: always reported.
            flag(number, line)
        target = top if section is None else sections[section][1]

        def problem() -> None:
            if section is None:  # section lines are reported already
                flag(number, line)

        arguments = words[1:]
        if directive == "require":
            if not arguments:
                problem()
                continue
            target["requires"].append((authz[-1] if authz else "requireany", arguments, number, line))
        elif directive == "order":
            target["legacy"] = True
            value = "".join(arguments).lower()
            if value not in ("deny,allow", "allow,deny"):
                problem()
                continue
            target["order"] = value
        elif directive in ("allow", "deny"):
            target["legacy"] = True
            if len(arguments) < 2 or arguments[0].lower() != "from":
                problem()
                continue
            values = target["allow_from"] if directive == "allow" else target["deny_from"]
            for value in arguments[1:]:
                if value.lower() == "all":
                    values.append("*")
                    continue
                address = _address(value)
                if address is None:
                    problem()
                    continue
                values.append(address)
        elif directive == "satisfy":
            if "".join(arguments).lower() != "all":
                problem()

    rule = _directives_rule(top, unsupported)
    if rule and rule["mode"] == "ip":
        for key in ("allow", "deny"):
            values = list(dict.fromkeys(rule[key]))
            if len(values) > MAX_ACCESS_ADDRESSES:
                # Too many entries to render safely; fail closed.
                unsupported.append({"line": 0, "directive": f"more than {MAX_ACCESS_ADDRESSES} addresses"})
                rule = {"mode": "deny"}
                break
            rule[key] = values
    grants = []
    for per_method, directives in sections:
        if not directives["requires"] and not directives["legacy"]:
            continue
        if per_method:
            directives = {
                "requires": top["requires"] + directives["requires"],
                "order": directives["order"] or top["order"],
                "allow_from": top["allow_from"] + directives["allow_from"],
                "deny_from": top["deny_from"] + directives["deny_from"],
                "legacy": top["legacy"] or directives["legacy"],
            }
        # Its lines are reported above; a section that only refuses access
        # is not applied (that never blocks a file Apache would serve).
        section_rule = _directives_rule(directives, [])
        if section_rule is not None and section_rule["mode"] != "deny":
            grants.append(section_rule)
    if grants:
        unsupported.append({"line": 0, "directive": _SECTION_NOTE})
    return {"rule": rule, "grants": grants, "unsupported": unsupported}


def _directives_rule(directives: dict, unsupported: list[dict]) -> dict | None:
    modern = _require_rule(directives["requires"], unsupported)
    legacy = (_legacy_rule(directives["order"], directives["allow_from"], directives["deny_from"])
              if directives["legacy"] else None)
    return _stricter(modern, legacy)


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


def _union(first: dict | None, second: dict | None) -> dict | None:
    """The least restrictive combination of two rules: it admits every
    address either one admits (None means unrestricted). Shapes that cannot
    be combined exactly become "allow", never a refusal."""
    if first is None or second is None:
        return None
    if first["mode"] == "deny":
        result = second
    elif second["mode"] == "deny":
        result = first
    elif "allow" in (first["mode"], second["mode"]):
        return {"mode": "allow"}
    elif first["deny"] == ["*"] and second["deny"] == ["*"]:
        # Two allow lists: either list's addresses.
        result = {"mode": "ip", "allow": list(dict.fromkeys(first["allow"] + second["allow"])), "deny": ["*"]}
    elif first["allow"] == ["*"] and second["allow"] == ["*"]:
        # Two block lists: only addresses both block stay blocked.
        deny = [value for value in first["deny"] if value in second["deny"]]
        result = {"mode": "ip", "allow": ["*"], "deny": deny} if deny else {"mode": "allow"}
    else:
        return {"mode": "allow"}
    if result["mode"] == "ip" and max(len(result["allow"]), len(result["deny"])) > MAX_ACCESS_ADDRESSES:
        return {"mode": "allow"}
    return result


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


def _expressible_ancestor(relative: str) -> str:
    """The nearest enclosing directory an OLS context can name ("" is the
    document root, always expressible)."""
    parts = relative.split("/")
    for depth in range(len(parts) - 1, 0, -1):
        ancestor = "/".join(parts[:depth])
        if PROTECTED_DIR_RE.match(ancestor):
            return ancestor
    return ""


def scan_access_rules(docroot: str) -> dict:
    """Walk one document root (bounded, never following symlinks) and parse
    every .htaccess. Returns {"rules": {relative_dir: rule}, "grants":
    {relative_dir: [rule, ...]}, "unsupported": [{"path", "line",
    "directive"}], "complete": bool, "read": {relative_dir, ...}};
    relative_dir is "" for the document root itself, and grants are keyed
    by the directory whose .htaccess holds them (also one an OLS context
    cannot name). "read" holds the directories whose .htaccess (or its
    absence) this scan saw; a scan that ran out of budget has not seen the
    others (see account_access_scans)."""
    rules: dict[str, dict] = {}
    grants: dict[str, list[dict]] = {}
    unsupported: list[dict] = []
    read: set[str] = set()
    complete = True
    try:
        root_fd = safeio.open_dir_beneath(docroot)
    except (OSError, safeio.UnsafePathError):
        return {"rules": rules, "grants": grants, "unsupported": unsupported, "complete": True, "read": read}
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
                    parsed = parse_access_file(text)
                    rule, issues, file_grants = parsed["rule"], parsed["unsupported"], parsed["grants"]
                    path = f"{relative}/.htaccess" if relative else ".htaccess"
                    if (rule is not None or file_grants) and relative and not PROTECTED_DIR_RE.match(relative):
                        # Not expressible as an OLS context path. What it
                        # allows stays a grant (applied at the nearest
                        # directory that is, see effective_access_contexts),
                        # so an enclosing deny cannot block those files.
                        issues.append({"line": 0, "directive": "directory name prevents OpenLiteSpeed "
                                       "from enforcing these rules; rename it using letters, digits, '.', '_' or '-'"})
                        if rule is not None and rule["mode"] != "deny":
                            file_grants = [*file_grants, rule]
                        rule = None
                    for issue in issues:
                        if len(unsupported) < MAX_REPORTED_ISSUES:
                            unsupported.append({"path": path, **issue})
                    if rule is not None:
                        rules[relative] = rule
                    if file_grants:
                        grants.setdefault(relative, []).extend(file_grants)
                read.add(relative)
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
    return {"rules": rules, "grants": grants, "unsupported": unsupported, "complete": complete, "read": read}


def effective_access_contexts(scan: dict) -> list[dict]:
    """Directories needing their own OLS context, outermost first. A
    directory whose own rule is "allow" only needs a context when an
    ancestor restricts access (a child's directives replace the parent's,
    as with Apache's default AuthMerging Off). Grants from <Files>/<Limit>
    sections in a directory or any ancestor apply to its files too, so they
    are combined (_union) into the rule of every directory they cover."""
    return _resolve_access(scan)[0]


def _resolve_access(scan: dict) -> tuple[list[dict], list[str]]:
    """(effective_access_contexts, directories whose own restriction a
    grant relaxed). The second list lets the report name every directory
    that is less protected than its .htaccess says, not only the one whose
    <Files>/<Limit> section caused it."""
    rules = scan["rules"]
    grants: dict[str, list[dict]] = {}
    for source, values in (scan.get("grants") or {}).items():
        target = source if not source or PROTECTED_DIR_RE.match(source) else _expressible_ancestor(source)
        grants.setdefault(target, []).extend(values)
    effective: dict[str, dict | None] = {}
    contexts = []
    relaxed: list[str] = []
    for relative in sorted(set(rules) | set(grants), key=lambda value: (value.count("/"), value)):
        parts = relative.split("/") if relative else []
        ancestors = ["/".join(parts[:depth]) for depth in range(len(parts) - 1, -1, -1)]
        inherited = next((effective[ancestor] for ancestor in ancestors if ancestor in effective), None)
        rule = rules.get(relative, inherited)
        for source in (relative, *ancestors):
            for grant in grants.get(source, ()):
                rule = _union(rule, grant)
        effective[relative] = rule
        own = rules.get(relative)
        if own is not None and own["mode"] != "allow" and rule != own:
            relaxed.append(relative)
        restricted_ancestor = inherited is not None and inherited["mode"] != "allow"
        if rule is None or (rule["mode"] == "allow" and not restricted_ancestor):
            continue
        contexts.append({"relative_path": relative, **rule})
    return contexts, relaxed


def _access_signature(scan: dict) -> tuple:
    return tuple(
        (context["relative_path"], context["mode"], tuple(context.get("allow", ())), tuple(context.get("deny", ())))
        for context in effective_access_contexts(scan)
    )


def _public_access_report(scan: dict) -> dict:
    contexts, relaxed = _resolve_access(scan)
    return {
        "access_rules": [
            {"directory": "/" + context["relative_path"], "mode": context["mode"],
             "allow": context.get("allow", []), "deny": context.get("deny", [])}
            for context in contexts
        ],
        "unsupported_access_directives": scan["unsupported"] + [
            {"path": f"{relative}/.htaccess" if relative else ".htaccess", "line": 0, "directive": _RELAXED_NOTE}
            for relative in relaxed
        ],
        "access_scan_complete": scan["complete"],
        # The scan stopped at its bound and the folders it did not re-read
        # keep the rules found by the last complete scan.
        "access_scan_reused_previous": bool(scan.get("reused_previous")),
    }


# --- Last complete scan ------------------------------------------------------
#
# The scan above is bounded (ACCESS_SCAN_MAX_*) and runs on every vhost
# render. A scan that runs out of budget has not read every .htaccess;
# dropping the rules it did not reach would make enforcement depend on disk
# speed and tree size from one render to the next. So the last complete scan
# of each document root is kept, per account (by id, which a rename keeps),
# in a root-only file beside the control-plane database, and fills in the
# directories a later bounded scan did not read. A complete scan always
# replaces it, so rules a customer removed disappear at the next one.

_SCAN_CACHE_LOCK = threading.Lock()
_SCAN_CACHE_MAX_BYTES = 8 * 1024 * 1024


def _scan_cache_path(account_id: int) -> Path:
    return Path(settings.db_path).parent / "htaccess-scans" / f"{int(account_id)}.json"


def _load_scan_cache(account_id: int) -> dict:
    try:
        fd = os.open(_scan_cache_path(account_id), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return {}
    except OSError:
        logger.warning("could not read the last .htaccess scan of account %s", account_id, exc_info=True)
        return {}
    try:
        with os.fdopen(fd, "rb") as stream:
            data = json.loads(stream.read(_SCAN_CACHE_MAX_BYTES + 1))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("docroots"), dict):
        return {}
    return data["docroots"]


def _store_scan_cache(account_id: int, docroots: dict) -> None:
    path = _scan_cache_path(account_id)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
    try:
        path.parent.mkdir(mode=0o700, exist_ok=True)
        if not docroots:
            path.unlink(missing_ok=True)
            return
        payload = json.dumps({"version": 1, "docroots": docroots}, separators=(",", ":")).encode()
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        # Best effort: losing it only means a later bounded scan cannot be
        # completed, never a failed render.
        logger.warning("could not keep the .htaccess scan of account %s", account_id, exc_info=True)
        temporary.unlink(missing_ok=True)


def _directory_exists(docroot: str, relative: str) -> bool:
    try:
        fd = safeio.open_dir_beneath(docroot, relative or ".")
    except (OSError, safeio.UnsafePathError):
        return False
    os.close(fd)
    return True


def _fill_from_previous(scan: dict, previous: dict, docroot: str) -> None:
    """Complete a bounded scan from the last complete one: directories it did
    not read (and that still exist) keep the rules and grants found then."""
    filled: set[str] = set()
    for key in ("rules", "grants"):
        for relative, value in (previous.get(key) or {}).items():
            if relative in scan["read"] or relative in scan[key] or not _directory_exists(docroot, relative):
                continue
            scan[key][relative] = value
            filled.add(relative)
    for issue in previous.get("unsupported") or []:
        directory = issue.get("path", "").removesuffix(".htaccess").rstrip("/")
        if directory in filled and len(scan["unsupported"]) < MAX_REPORTED_ISSUES:
            scan["unsupported"].append(issue)
    scan["reused_previous"] = bool(filled)


def account_access_scans(account_id: int, docroots: list[str], *, prune: bool = False) -> dict[str, dict]:
    """scan_access_rules for each of one account's document roots, with a
    bounded scan that ran out of budget completed from the last complete
    scan of that root. prune (every document root of the account passed)
    forgets roots the account no longer has."""
    scans: dict[str, dict] = {}
    for docroot in dict.fromkeys(docroots):
        try:
            scans[docroot] = scan_access_rules(docroot)
        except Exception:
            logger.exception("could not read .htaccess access rules under %s", docroot)
            scans[docroot] = {"rules": {}, "grants": {}, "unsupported": [], "complete": False, "read": set()}
    with _SCAN_CACHE_LOCK:
        cache = _load_scan_cache(account_id)
        updated = dict(cache)
        for docroot, scan in scans.items():
            key = os.path.realpath(docroot)
            if scan["complete"]:
                updated[key] = {"rules": scan["rules"], "grants": scan["grants"], "unsupported": scan["unsupported"]}
            elif isinstance(cache.get(key), dict):
                _fill_from_previous(scan, cache[key], docroot)
        if prune:
            keep = {os.path.realpath(docroot) for docroot in scans}
            updated = {key: value for key, value in updated.items() if key in keep}
        if updated != cache:
            _store_scan_cache(account_id, updated)
    return scans


def forget_account_scans(account_id: int) -> None:
    """Account termination: drop its kept scans."""
    with _SCAN_CACHE_LOCK:
        try:
            _scan_cache_path(account_id).unlink(missing_ok=True)
        except OSError:
            logger.warning("could not remove the .htaccess scans of account %s", account_id, exc_info=True)


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
            signature = _access_signature(account_access_scans(account_id, [docroot])[docroot])
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
