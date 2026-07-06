"""Phase 7b feature 1: import a standard cPanel/WHM full-backup tarball into
a brand-new Forgehost account.

Format note (read before touching this file): this sandbox has no licensed
cPanel/WHM instance to generate a genuine backup from, so there is no real
sample to reverse-engineer against -- the same constraint this project has
already hit for other external systems (no owned domain for SSL, no real
attacker to unban for fail2ban) and handled the same way each time: build
against the mechanism's own public, documented shape, and disclose the
substitution honestly rather than silently assume it's exact. The parsing
here follows WHM/cPanel's own publicly documented "Backup File and Directory
Structure" layout:

  <root>/homedir/                 -- full home directory tree (public_html, etc.)
  <root>/mysql/<dbname>.sql       -- one plain-text mysqldump per database
  <root>/userdata/main.yaml       -- main_domain/addon_domains/sub_domains/parked_domains
  <root>/userdata/<domain>.yaml   -- per-domain docroot/php version (optional)
  <root>/dnszones/<domain>.db     -- BIND zone file per domain
  <root>/ssl/certs/<domain>.crt   -- SSL certificate (paired with ssl/keys/<domain>.key)
  <root>/ssl/keys/<domain>.key
  <root>/cron/<cpuser>            -- raw crontab text
  <root>/<cpuser>.yaml            -- top-level cpuser account summary (DOMAIN/EMAIL/IP)
  <root>/homedir/mail/<domain>/<local>/{cur,new,tmp}  -- Maildir mailboxes
  <root>/homedir/etc/<domain>/passwd                  -- cPanel-style FTP account list

`<root>` itself may or may not be wrapped in one extra top-level directory
(a full `cpmove-<user>.tar.gz` and a WHM "backup-<ts>_<user>.tar.gz" differ
here) -- _find_content_root handles both.

Every import step is wrapped so one failing/unsupported item is recorded and
skipped rather than aborting the whole job (goal: "fail gracefully on
unsupported items, import rest") -- only a handful of genuinely fatal, early
steps (fetching/extracting the archive, creating the target account) abort
the job outright, since nothing else is meaningful without them.

This module runs inside forgehostd (root) and therefore calls the same
handlers_*.py functions directly that backup.py's own full-restore path
calls (daemon/backup.py's _restore_full is the closest existing precedent
in this codebase, both in spirit -- "recreate an account from an external
description of it" -- and in several of the exact functions reused here).
"""
from __future__ import annotations

import logging
import os
import pwd
import re
import shutil
import tarfile
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import NameOID
from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, CpanelImportJob, Domain, utcnow
from shared.validation import (
    ValidationError,
    generate_strong_password,
    validate_db_identifier,
    validate_domain,
    validate_mailbox_local_part,
    validate_username,
)

from daemon import handlers_account, handlers_cron, handlers_database, handlers_dns, handlers_domain, handlers_ftp, handlers_mail, mariadb, ols
from daemon.backup import _write_mysql_defaults_file
from daemon.procutil import run
from daemon.wordpress import _php_str

logger = logging.getLogger("forgehostd.cpanel_import")

_executor = ThreadPoolExecutor(max_workers=settings.cpanel_import_concurrency, thread_name_prefix="cpanel-import")

MAX_RESULTS_DETAIL_LEN = 500


class CpanelImportError(Exception):
    pass


# --- job bookkeeping ---------------------------------------------------------


def _job_to_dict(job: CpanelImportJob, *, include_source_ref: bool = False) -> dict:
    d = {
        "id": job.id,
        "username": job.username,
        "source": job.source,
        "status": job.status,
        "progress_message": job.progress_message,
        "results": job.results,
        "error": job.error,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }
    if include_source_ref:
        d["source_ref"] = job.source_ref
    return d


def _update_job(job_id: int, **fields) -> None:
    with write_session() as session:
        job = session.get(CpanelImportJob, job_id)
        if job is None:
            return
        for key, value in fields.items():
            setattr(job, key, value)


def _append_result(job_id: int, item: str, status: str, detail: str = "") -> None:
    detail = (detail or "")[:MAX_RESULTS_DETAIL_LEN]
    with write_session() as session:
        job = session.get(CpanelImportJob, job_id)
        if job is None:
            return
        job.results = [*job.results, {"item": item, "status": status, "detail": detail}]
    logger.info("cpanel import job %d: %s -> %s (%s)", job_id, item, status, detail)


def _run_step(job_id: int, item: str, fn) -> None:
    """Runs one import sub-step, recording ok/failed/skipped -- never lets an
    individual item's exception escape and abort the whole job (goal:
    "fail gracefully on unsupported items, import rest")."""
    try:
        detail = fn()
        _append_result(job_id, item, "ok", detail or "")
    except _Skip as skip:
        _append_result(job_id, item, "skipped", str(skip))
    except Exception as exc:  # noqa: BLE001 - one item's failure must not abort the job
        logger.exception("cpanel import job %d: item '%s' failed", job_id, item)
        _append_result(job_id, item, "failed", str(exc))


class _Skip(Exception):
    """Raised by a step to report itself as deliberately skipped (not
    supported / not present in this backup), distinct from a genuine
    failure -- surfaces as status="skipped" rather than "failed" in the
    per-item report."""


def trigger_import(params: dict) -> dict:
    username = validate_username(params["username"])
    source = params.get("source", "upload")
    if source not in ("upload", "url"):
        raise ValidationError("source must be 'upload' or 'url'")
    source_ref = params.get("source_ref") or params.get("url") or params.get("upload_path")
    if not source_ref:
        raise ValidationError("source_ref (uploaded file path or URL) is required")

    with write_session() as session:
        if session.scalar(select(Account).where(Account.username == username)) is not None:
            raise CpanelImportError(
                f"account '{username}' already exists -- cPanel import only creates a brand-new account, "
                "it does not merge into an existing one"
            )
        existing_active = session.scalar(
            select(CpanelImportJob).where(
                CpanelImportJob.username == username, CpanelImportJob.status.in_(("pending", "running"))
            )
        )
        if existing_active is not None:
            raise CpanelImportError(f"an import for '{username}' is already in progress (job {existing_active.id})")

        job = CpanelImportJob(username=username, source=source, source_ref=source_ref, status="pending", progress_message="queued")
        session.add(job)
        session.flush()
        job_id = job.id
        result = _job_to_dict(job)

    _executor.submit(_run_import_job, job_id, dict(params, source=source, source_ref=source_ref))
    return result


def get_job(params: dict) -> dict:
    """job_id is a small sequential integer, not a capability -- same
    ownership-check convention as every other async job's get_job in this
    project (WordPressJob/BackupJob's own docstrings explain why:
    docs/CHECKPOINT-phase4-0b-cross-account-idor.md)."""
    job_id = int(params["job_id"])
    username = validate_username(params["username"])
    with write_session() as session:
        job = session.get(CpanelImportJob, job_id)
        if job is None or job.username != username:
            raise CpanelImportError(f"cPanel import job {job_id} not found")
        return _job_to_dict(job)


def list_jobs(params: dict | None = None) -> dict:
    params = params or {}
    username = params.get("username")
    with write_session() as session:
        query = select(CpanelImportJob).order_by(CpanelImportJob.started_at.desc())
        if username:
            query = query.where(CpanelImportJob.username == username)
        rows = session.scalars(query.limit(int(params.get("limit", 50)))).all()
        return {"jobs": [_job_to_dict(j) for j in rows]}


# --- archive acquisition + extraction ----------------------------------------


def _obtain_archive(job_id: int, source: str, source_ref: str, work_dir: Path) -> Path:
    if source == "upload":
        upload_path = Path(source_ref)
        if not upload_path.is_file():
            raise CpanelImportError(f"uploaded file '{source_ref}' not found")
        return upload_path

    _update_job(job_id, progress_message=f"downloading backup from {source_ref}")
    dest = work_dir / "download.tar.gz"
    max_bytes = settings.cpanel_import_max_upload_bytes
    written = 0
    with httpx.stream("GET", source_ref, timeout=600.0, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes():
                written += len(chunk)
                if written > max_bytes:
                    raise CpanelImportError(f"backup download exceeds the {max_bytes} byte limit")
                f.write(chunk)
    return dest


def _extract_archive(archive_path: Path, extract_dir: Path) -> None:
    try:
        with tarfile.open(archive_path) as tf:
            # filter="data" (Python 3.12+): rejects absolute paths, ".."
            # traversal, and device/special files -- the same tar-slip
            # defense daemon/backup.py's own restore path uses for an
            # externally-sourced artifact (this one more so: a cPanel
            # backup is a third-party file this daemon did not produce
            # itself, extracted as root before anything is chowned away
            # from root).
            tf.extractall(extract_dir, filter="data")
    except tarfile.TarError as exc:
        raise CpanelImportError(f"'{archive_path}' is not a readable tar/tar.gz archive: {exc}") from exc


def _find_content_root(extract_dir: Path) -> Path:
    """A full cpmove backup wraps everything in one top-level directory
    (cpmove-<user>/ or backup-<ts>_<user>/); a raw WHM pkgacct-style backup
    does not. Detect by looking for the known top-level entries directly,
    descending one level if none of them are found there but exactly one
    subdirectory is."""
    known = ("homedir", "mysql", "userdata", "dnszones", "cron", "ssl")
    if any((extract_dir / name).exists() for name in known):
        return extract_dir
    subdirs = [p for p in extract_dir.iterdir() if p.is_dir()]
    if len(subdirs) == 1 and any((subdirs[0] / name).exists() for name in known):
        return subdirs[0]
    # Neither shape matched -- still return extract_dir itself so downstream
    # per-item parsers each independently report "not found" rather than
    # this function guessing wrong and hiding content that IS there.
    return extract_dir


# --- account info -------------------------------------------------------------


def _load_yaml_lenient(path: Path) -> dict:
    """cPanel's own userdata/cpuser files are real YAML in modern versions,
    but this project has no way to confirm every historical cPanel version
    it might ever see produced strict YAML -- falls back to simple
    KEY=value / KEY: value line parsing if yaml.safe_load chokes, rather
    than aborting the whole import over one malformed metadata file."""
    text = path.read_text(errors="replace")
    try:
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            return data
    except yaml.YAMLError:
        pass
    result: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in ("=", ":"):
            if sep in line:
                key, _, value = line.partition(sep)
                result[key.strip().strip('"')] = value.strip().strip('"')
                break
    return result


def _parse_account_info(root: Path) -> dict:
    """Best-effort extraction of (old_username, main_domain, email) from
    whichever metadata file is present -- tried in order of how reliable
    each source is, each one optional."""
    info: dict = {}

    main_yaml = root / "userdata" / "main.yaml"
    if main_yaml.is_file():
        data = _load_yaml_lenient(main_yaml)
        if data.get("main_domain"):
            info["main_domain"] = data["main_domain"]
        info["addon_domains"] = list((data.get("addon_domains") or {}).keys()) if isinstance(data.get("addon_domains"), dict) else []
        info["sub_domains"] = data.get("sub_domains") or []
        info["parked_domains"] = data.get("parked_domains") or []

    cpuser_candidates = list(root.glob("*.yaml")) + list((root / "cp").glob("*"))
    for candidate in cpuser_candidates:
        data = _load_yaml_lenient(candidate)
        old_username = data.get("USER") or data.get("user") or candidate.stem
        if re.match(r"\A[A-Za-z][A-Za-z0-9]{0,15}\Z", old_username or ""):
            info.setdefault("old_username", old_username)
        for key in ("DOMAIN", "domain"):
            if data.get(key) and "main_domain" not in info:
                info["main_domain"] = data[key]
        for key in ("EMAIL", "CONTACTEMAIL", "email"):
            if data.get(key):
                info.setdefault("email", data[key])
        break  # only the first candidate is used -- a backup has at most one

    if "main_domain" not in info:
        # Last-resort fallbacks, each cheap and each independently disclosed
        # in the per-item report if used (via the "account" step's detail
        # string) rather than silently guessed.
        zones = sorted((root / "dnszones").glob("*.db")) if (root / "dnszones").is_dir() else []
        if zones:
            info["main_domain"] = zones[0].stem
        elif (root / "userdata").is_dir():
            per_domain = [p for p in (root / "userdata").glob("*.yaml") if p.stem not in ("main",) and not p.stem.endswith("_ssl")]
            if per_domain:
                info["main_domain"] = per_domain[0].stem

    return info


# --- domains ------------------------------------------------------------------


def _parse_domains(root: Path, info: dict) -> list[dict]:
    domains: list[dict] = []
    seen: set[str] = set()

    def _add(name: str | None, kind: str) -> None:
        if not name:
            return
        try:
            clean = validate_domain(name)
        except ValidationError:
            return
        if clean in seen:
            return
        seen.add(clean)
        domains.append({"domain": clean, "kind": kind})

    _add(info.get("main_domain"), "primary")
    for name in info.get("addon_domains", []):
        _add(name, "addon")
    for name in info.get("sub_domains", []):
        _add(name, "subdomain")
    for name in info.get("parked_domains", []):
        _add(name, "addon")

    userdata_dir = root / "userdata"
    if userdata_dir.is_dir():
        for path in sorted(userdata_dir.glob("*.yaml")):
            if path.stem in ("main",) or path.stem.lower().endswith("_ssl"):
                continue
            _add(path.stem, "addon")

    return domains


def _userdata_docroot(root: Path, domain: str) -> str | None:
    path = root / "userdata" / f"{domain}.yaml"
    if not path.is_file():
        return None
    data = _load_yaml_lenient(path)
    return data.get("documentroot") or data.get("docroot")


# --- homedir / files -----------------------------------------------------------


def _copy_homedir(root: Path, username: str) -> str:
    homedir = root / "homedir"
    if not homedir.is_dir():
        raise _Skip("backup has no homedir/ directory -- nothing to copy")
    home_base = Path(settings.home_base) / username
    copied = 0
    for entry in homedir.iterdir():
        # mail/ is handled separately by _import_mailboxes (moved into
        # /var/vmail, owned by the vmail user, not left under the account's
        # own home) -- and etc/ holds cPanel's own internal per-domain FTP/
        # DNS bookkeeping (handled by _parse_ftp_accounts), not customer
        # site content, so both are deliberately excluded from the plain
        # recursive copy rather than dumped as-is into the new account's home.
        if entry.name in ("mail", "etc", ".cpanel"):
            continue
        dest = home_base / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, dest)
        copied += 1
    pw = pwd.getpwnam(username)
    run(["chown", "-R", f"{pw.pw_uid}:{pw.pw_gid}", str(home_base)], timeout=1800, check=True)
    return f"copied {copied} top-level homedir entries to {home_base}"


def _relocate_addon_docroot(root: Path, username: str, domain: str, docroot: str) -> str | None:
    """cPanel records each addon/parked domain's own chosen docroot in
    userdata/<domain>.yaml, which frequently does NOT match Forgehost's own
    <home>/<domain> convention (add_domain() always creates the latter).
    If the cPanel-recorded docroot exists under the copied homedir and is
    not already the domain's Forgehost docroot, move its contents into
    place so the imported site's actual files end up where Forgehost's own
    vhost rendering expects them."""
    recorded = _userdata_docroot(root, domain)
    if not recorded:
        return None
    home_base = Path(settings.home_base) / username
    # cPanel stores documentroot as an absolute path on the ORIGINAL server
    # (e.g. /home/olduser/public_html/sub) -- only the portion after the
    # old account's home directory is meaningful here.
    relative = re.sub(r"\A/home[^/]*/[^/]+/?", "", recorded).strip("/")
    if not relative:
        return None
    source = home_base / relative
    target = Path(docroot)
    if not source.is_dir() or source.resolve() == target.resolve():
        return None
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        dest = target / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)
    pw = pwd.getpwnam(username)
    run(["chown", "-R", f"{pw.pw_uid}:{pw.pw_gid}", str(target)], timeout=600, check=True)
    return f"relocated '{relative}' -> '{docroot}'"


# --- databases ------------------------------------------------------------------


def _parse_mysql_dumps(root: Path) -> list[Path]:
    mysql_dir = root / "mysql"
    if not mysql_dir.is_dir():
        return []
    return sorted(p for p in mysql_dir.glob("*.sql"))


def _db_suffix_from_dump(dump_path: Path, old_username: str | None) -> str:
    name = dump_path.stem
    if old_username and name.startswith(f"{old_username}_"):
        name = name[len(old_username) + 1 :]
    # Forgehost re-derives the full name as <new_username>_<suffix> --
    # validate_db_identifier is applied by handlers_database.create_database
    # itself; a name that doesn't fit is truncated here so an oversized-but-
    # otherwise-fine suffix doesn't fail the whole item on length alone.
    name = re.sub(r"[^a-z0-9_]", "_", name.lower())
    if not name or not name[0].isalpha():
        name = f"db_{name}"
    return name[:40]


_DUMP_DB_CONTEXT_RE = re.compile(r"^\s*(CREATE\s+DATABASE|USE|DROP\s+DATABASE)\b.*;\s*$", re.IGNORECASE)


def _strip_dump_database_context(sql_text: str) -> str:
    """Real, well-known cPanel/WHM migration gotcha (defended against here
    even without a genuine cPanel backup to confirm it empirically against
    -- 'conservative/secure, document why'): a mysqldump invoked with
    `--databases` (a common way full-account backup tooling generates
    per-database dump files) prepends its own `CREATE DATABASE ...;`/
    `USE ...;` lines naming the ORIGINAL database. Fed to `mysql
    <target_db>` as-is, the dump's own `USE` statement silently overrides
    the caller-specified target for every statement after it -- the import
    would then either fail with an access-denied error (forgehost_daemon's
    grants are scoped per-database, HOSTED_DB_PRIVILEGES) or, worse, if a
    same-named database happened to already exist, write into the wrong
    one entirely. Stripped line-by-line before import so this dump always
    lands in the caller-specified `db_name`, regardless of what the
    original dump's own header says."""
    return "\n".join(line for line in sql_text.splitlines() if not _DUMP_DB_CONTEXT_RE.match(line))


def _import_mysql_dump(db_name: str, dump_path: Path) -> None:
    cnf_path = _write_mysql_defaults_file()
    try:
        sql_text = _strip_dump_database_context(dump_path.read_text(errors="replace"))
        result = run(["mysql", f"--defaults-extra-file={cnf_path}", db_name], input_text=sql_text, timeout=1800)
        if not result.ok:
            raise CpanelImportError(f"mysql import failed for '{db_name}': {result.stderr.strip()}")
    finally:
        os.unlink(cnf_path)


# --- WordPress wp-config rewrite (best effort) ---------------------------------

_WP_DEFINE_RE = {
    "DB_NAME": re.compile(r"(define\(\s*['\"]DB_NAME['\"]\s*,\s*)(['\"]).*?\2(\s*\)\s*;)"),
    "DB_USER": re.compile(r"(define\(\s*['\"]DB_USER['\"]\s*,\s*)(['\"]).*?\2(\s*\)\s*;)"),
    "DB_PASSWORD": re.compile(r"(define\(\s*['\"]DB_PASSWORD['\"]\s*,\s*)(['\"]).*?\2(\s*\)\s*;)"),
    "DB_HOST": re.compile(r"(define\(\s*['\"]DB_HOST['\"]\s*,\s*)(['\"]).*?\2(\s*\)\s*;)"),
}


def _rewrite_wp_config(docroot: str, db_name: str, db_user: str, db_password: str) -> str:
    """In-place field substitution, not full regeneration (unlike a fresh
    daemon/wordpress.py install): a migrated site's salts, table prefix, and
    any custom constants (WP_HOME, WP_SITEURL, plugin-added defines) must
    survive the import untouched -- only the DB connection constants
    actually changed (new database, new credentials on this server).
    Reuses wordpress._php_str for the replacement literal specifically
    because a generated MariaDB password can itself contain a literal '$',
    which a naive replacement string would let PHP's own double-quoted-
    string interpolation corrupt (the exact bug documented in
    wordpress._php_str's own docstring)."""
    path = os.path.join(docroot, "wp-config.php")
    if not os.path.isfile(path):
        raise _Skip("no wp-config.php found -- not a WordPress site (or not at this docroot)")
    content = Path(path).read_text(errors="replace")
    replacements = {"DB_NAME": db_name, "DB_USER": db_user, "DB_PASSWORD": db_password, "DB_HOST": f"localhost:{settings.mariadb_socket}"}
    changed = []
    missing = []
    for key, pattern in _WP_DEFINE_RE.items():
        value = replacements[key]

        def _sub(m, v=value):
            return f"{m.group(1)}{_php_str(v)}{m.group(3)}"

        new_content, count = pattern.subn(_sub, content)
        if count:
            content = new_content
            changed.append(key)
        else:
            missing.append(key)
    if not changed:
        raise CpanelImportError("wp-config.php found but no DB_NAME/DB_USER/DB_PASSWORD/DB_HOST define() lines matched")
    if missing:
        # Real bug found by adversarial review (this codebase's own
        # "validate config before apply, rollback on failure" rule,
        # applied to a gap the original "at least one matched" check
        # didn't cover): a site whose imported database now lives under a
        # NEW name/user/password on this server needs ALL FOUR constants
        # updated consistently -- e.g. a wp-config left with the OLD
        # DB_NAME but the NEW DB_USER/DB_PASSWORD would silently produce a
        # site that can't connect at all (the new db user has no grants on
        # whatever the old DB_NAME string still says), with no obvious
        # cause. Reported as a failure of this specific item (still
        # skippable/reportable per-item, per the goal's own "fail
        # gracefully... import rest" rule -- this raises up to _run_step,
        # which records it as "failed" for this one wordpress:<domain>
        # item without aborting the rest of the import) rather than left
        # as a silently half-migrated site.
        raise CpanelImportError(
            f"wp-config.php is missing an expected define() for: {', '.join(missing)} -- "
            "refusing to leave it with inconsistent database credentials"
        )
    Path(path).write_text(content)
    return f"rewrote {', '.join(changed)} in wp-config.php"


# --- DNS zones ------------------------------------------------------------------


def _parse_bind_zone_records(zone_path: Path, domain: str) -> list[dict]:
    import dns.exception
    import dns.rdatatype
    import dns.zone

    try:
        zone = dns.zone.from_file(str(zone_path), origin=domain, check_origin=False)
    except (dns.exception.DNSException, OSError) as exc:
        raise CpanelImportError(f"could not parse BIND zone file '{zone_path.name}': {exc}") from exc

    records: list[dict] = []
    supported = {"A", "AAAA", "CNAME", "MX", "TXT"}
    origin = zone.origin
    for name, node in zone.nodes.items():
        label = str(name)
        for rdataset in node.rdatasets:
            rtype = dns.rdatatype.to_text(rdataset.rdtype)
            if rtype not in supported:
                continue
            for rdata in rdataset:
                # Real bug found reviewing this function (confirmed with a
                # real dnspython parse, not just inspection): dns.zone.from_file
                # relativizes every name to the zone's own origin by default,
                # including a Name *value* referenced from an rdata field
                # (MX's exchange, CNAME's target) -- so a zone file's fully
                # qualified "mail.example.com." silently comes back from
                # str() as just "mail", which is not a valid absolute target
                # and would be written into PowerDNS as broken record
                # content. derelativize(origin) restores the fully qualified
                # form regardless of whether the parsed name happened to be
                # relative or already absolute in the source file.
                if rtype == "MX":
                    exchange = rdata.exchange.derelativize(origin)
                    value = f"{rdata.preference} {exchange}"
                elif rtype == "CNAME":
                    value = str(rdata.target.derelativize(origin))
                elif rtype == "TXT":
                    value = b"".join(rdata.strings).decode("utf-8", errors="replace")
                else:
                    value = str(rdata)
                records.append({"subdomain": label, "type": rtype, "value": value})
    return records


def _import_dns_zone(username: str, domain: str, zone_path: Path) -> str:
    handlers_dns.create_zone({"username": username, "domain": domain})
    records = _parse_bind_zone_records(zone_path, domain)
    imported = 0
    for rec in records:
        try:
            handlers_dns.set_record(
                {"domain": domain, "subdomain": rec["subdomain"], "type": rec["type"], "values": [rec["value"]]}
            )
            imported += 1
        except Exception:  # noqa: BLE001 - one bad record must not drop the whole zone
            logger.exception("cpanel import: failed to import %s record for '%s'", rec["type"], domain)
    return f"created managed zone '{domain}' with {imported}/{len(records)} records imported"


# --- SSL certificates -----------------------------------------------------------


def _parse_ssl_certs(root: Path) -> list[tuple[str, Path, Path]]:
    certs_dir = root / "ssl" / "certs"
    keys_dir = root / "ssl" / "keys"
    if not certs_dir.is_dir() or not keys_dir.is_dir():
        return []
    found = []
    for cert_path in sorted(certs_dir.glob("*.crt")):
        key_path = keys_dir / f"{cert_path.stem}.key"
        if key_path.is_file():
            found.append((cert_path.stem, cert_path, key_path))
    return found


def _import_ssl_cert(username: str, domain: str, cert_path: Path, key_path: Path) -> str:
    cert_bytes = cert_path.read_bytes()
    key_bytes = key_path.read_bytes()
    try:
        cert = x509.load_pem_x509_certificate(cert_bytes)
        private_key = serialization.load_pem_private_key(key_bytes, password=None)
    except (ValueError, TypeError) as exc:
        raise _Skip(f"not a valid PEM certificate/key pair: {exc}")

    if cert.not_valid_after_utc < utcnow():
        raise _Skip(f"certificate expired on {cert.not_valid_after_utc.date()} -- not imported")

    sans = []
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        sans = ext.value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        pass
    common_name = ""
    try:
        common_name = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    except IndexError:
        pass
    covered = {common_name, *sans}
    if domain not in covered and not any(
        name.startswith("*.") and domain.endswith(name[1:]) for name in covered
    ):
        raise _Skip(f"certificate does not cover '{domain}' (covers: {sorted(n for n in covered if n)})")

    # Public key match: confirms the private key genuinely belongs to this
    # certificate before it's ever installed -- a mismatched pair would
    # silently break TLS the moment OLS tries to use it.
    cert_public_numbers = cert.public_key().public_numbers()
    key_public_numbers = private_key.public_key().public_numbers()
    if cert_public_numbers != key_public_numbers:
        raise _Skip("certificate and private key do not match -- not imported")

    cert_dir = Path(f"/etc/letsencrypt/live/{domain}")
    cert_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
    (cert_dir / "fullchain.pem").write_bytes(cert_bytes)
    (cert_dir / "privkey.pem").write_bytes(key_bytes)
    os.chmod(cert_dir / "privkey.pem", 0o600)

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        domain_row = session.scalar(select(Domain).where(Domain.domain == domain, Domain.account_id == account.id))
        if domain_row is None:
            raise CpanelImportError(f"domain '{domain}' has no Domain row -- import its domain item first")
        domain_row.ssl_status = "active"
        domain_row.ssl_is_wildcard = any(n.startswith("*.") for n in covered)
        session.flush()
        account_snapshot = account

    ols.refresh_vhost(account_snapshot)
    return f"installed imported certificate for '{domain}' (expires {cert.not_valid_after_utc.date()})"


# --- mailboxes --------------------------------------------------------------


def _parse_mailboxes(root: Path, domains: list[str]) -> list[tuple[str, str]]:
    mail_dir = root / "homedir" / "mail"
    if not mail_dir.is_dir():
        return []
    found = []
    for domain_dir in mail_dir.iterdir():
        if not domain_dir.is_dir() or domain_dir.name not in domains:
            continue
        for mailbox_dir in domain_dir.iterdir():
            if mailbox_dir.is_dir() and any((mailbox_dir / sub).is_dir() for sub in ("cur", "new", "tmp")):
                found.append((domain_dir.name, mailbox_dir.name))
    return found


def _import_mailbox(domain: str, local_part: str, maildir_src: Path) -> str:
    local_part = validate_mailbox_local_part(local_part)
    # Original mailbox passwords are cPanel-side crypt hashes in a scheme
    # this project's Dovecot config isn't guaranteed to share -- same
    # "unrecoverable, so issue a fresh one" posture daemon/backup.py's own
    # full-restore path already takes for mail_users (see its comment),
    # applied here for the identical reason.
    password = generate_strong_password()
    handlers_mail.create_mailbox({"domain": domain, "local_part": local_part, "password": password})
    dest = Path(settings.mail_base) / domain / local_part
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(maildir_src, dest)
    run(["chown", "-R", "vmail:vmail", str(dest)], timeout=300, check=True)
    return f"imported mailbox with a new random password (original password is not recoverable from a cPanel backup)"


# --- FTP accounts -------------------------------------------------------------

_FTP_PASSWD_LINE_RE = re.compile(r"\A([^:]+):[^:]*:(\d+):(\d+):[^:]*:([^:]*):")


def _parse_ftp_accounts(root: Path, domains: list[str]) -> list[tuple[str, str]]:
    """Best effort: cPanel's per-domain FTP account list
    (homedir/etc/<domain>/passwd) is only present in some backup
    configurations -- accounts found this way get a fresh random password
    (see _import_ftp_account) since the stored hash uses cPanel's own
    scheme, not necessarily PureDB's."""
    found = []
    etc_dir = root / "homedir" / "etc"
    if not etc_dir.is_dir():
        return found
    for domain in domains:
        passwd_file = etc_dir / domain / "passwd"
        if not passwd_file.is_file():
            continue
        for line in passwd_file.read_text(errors="replace").splitlines():
            m = _FTP_PASSWD_LINE_RE.match(line)
            if m:
                found.append((m.group(1), domain))
    return found


def _import_ftp_account(username: str, ftp_login: str, domain: str) -> str:
    label = re.sub(r"[^a-z0-9_]", "_", ftp_login.split("@")[0].lower())[:32] or "ftp"
    password = generate_strong_password()
    handlers_ftp.create_ftp_account({"username": username, "label": label, "path": "", "password": password})
    return f"created FTP sub-account '{username}_{label}' with a new random password (original hash not portable)"


# --- cron jobs ------------------------------------------------------------------

_CRON_LINE_RE = re.compile(r"\A\s*(\S+\s+\S+\s+\S+\s+\S+\s+\S+)\s+(.+)\Z")


def _parse_cron(root: Path, old_username: str | None) -> list[tuple[str, str]]:
    cron_dir = root / "cron"
    if not cron_dir.is_dir():
        return []
    candidates = [cron_dir / old_username] if old_username and (cron_dir / old_username).is_file() else list(cron_dir.iterdir())
    jobs: list[tuple[str, str]] = []
    for path in candidates:
        if not path.is_file():
            continue
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            first_token = line.split(None, 1)[0]
            if "=" in first_token:
                continue  # a MAILTO=.../env-var assignment line, not a job
            m = _CRON_LINE_RE.match(line)
            if m:
                jobs.append((m.group(1), m.group(2)))
        break  # only the first matching crontab file is used
    return jobs


# --- orchestration --------------------------------------------------------------


def _add_domain_step(username: str, domain_entry: dict) -> str:
    handlers_domain.add_domain({"username": username, "domain": domain_entry["domain"], "kind": domain_entry["kind"]})
    return f"added as {domain_entry['kind']}"


def _reassert_docroot_perms_step(username: str, docroot: str) -> str:
    """Real bug found live while reviewing this module (not by running it --
    confirmed directly with a throwaway shutil.copytree call): `_copy_homedir`
    uses `shutil.copytree(src, dst, dirs_exist_ok=True)`, and shutil's own
    `copystat` call on an *already-existing* target directory overwrites its
    mode bits to match the source -- e.g. a cPanel `public_html` commonly
    ships mode 0755, silently widening the docroot from the required 0750
    back to world-readable the instant homedir files are copied in. This is
    the exact vulnerability ARCHITECTURE.md SS6 documents fixing (any local
    Linux account can then `cat` another account's files), reopened by a
    step that runs on every single import. Same root cause class, same fix,
    as daemon/backup.py's own full-restore path re-running ensure_docroot
    after extracting a plain tar that doesn't preserve ACLs either -- files
    restored from an external archive must never be trusted to leave the
    account's permission model intact, re-assert it explicitly afterward."""
    handlers_domain.ensure_docroot(username, docroot)
    return "re-asserted docroot mode/ACL after copying in homedir content"


def _relocate_docroot_step(root: Path, username: str, domain: str) -> str:
    docroot = _domain_docroot(username, domain)
    detail = _relocate_addon_docroot(root, username, domain, docroot)
    return detail or "no relocation needed (docroot already matches, or none recorded)"


def _import_database_step(username: str, dump_path: Path, old_username: str | None, db_name_map: dict) -> str:
    suffix = _db_suffix_from_dump(dump_path, old_username)
    grant = handlers_database.create_database({"username": username, "name": suffix})
    _import_mysql_dump(grant["db_name"], dump_path)
    db_name_map[dump_path.stem] = grant["db_name"]
    return f"imported into '{grant['db_name']}'"


def _wordpress_rewrite_step(username: str, domain: str, db_name_map: dict[str, str]) -> str:
    """Only one imported database is realistically guessable without
    actually reading the pre-rewrite wp-config.php's own DB_NAME -- try
    that first, then fall back to the single-database case (the
    overwhelmingly common shape for a one-site cPanel account backup this
    feature targets); anything more ambiguous is reported as skipped
    rather than guessed at."""
    if not db_name_map:
        raise _Skip("no databases were imported to link this site to")
    docroot = _domain_docroot(username, domain)
    wp_config = Path(docroot) / "wp-config.php"
    chosen = None
    if wp_config.is_file():
        original = wp_config.read_text(errors="replace")
        m = re.search(r"define\(\s*['\"]DB_NAME['\"]\s*,\s*['\"]([^'\"]+)['\"]", original)
        if m and m.group(1) in db_name_map:
            chosen = db_name_map[m.group(1)]
    if chosen is None and len(db_name_map) == 1:
        chosen = next(iter(db_name_map.values()))
    if chosen is None:
        raise _Skip("multiple databases imported and none could be matched to this site's wp-config.php")
    db_user = chosen  # Forgehost's own 1-DB-1-user convention (handlers_database.create_database)
    new_password = mariadb.generate_password()
    mariadb.set_password(db_user, new_password)
    return _rewrite_wp_config(docroot, chosen, db_user, new_password)


def _add_cron_step(username: str, schedule: str, command: str) -> str:
    handlers_cron.add_cron_job({"username": username, "schedule": schedule, "command": command, "label": "cpanel-import"})
    return "added"


def _run_import_job(job_id: int, params: dict) -> None:
    username = params["username"]
    source = params["source"]
    source_ref = params["source_ref"]

    _update_job(job_id, status="running", progress_message="fetching backup archive")
    try:
        # The staging root holds transient extracted backup contents (DB
        # dumps, mailbox data) and is root-only (0700), same posture as
        # backup_staging_dir. Create it self-healingly here rather than
        # relying on a separate install step: a missing dir must fail the
        # job cleanly, not leave mkdtemp raising *outside* the handler below
        # (which stranded the job at "fetching backup archive"/running with
        # no error and no way for the poller to see it had died).
        os.makedirs(settings.cpanel_import_staging_dir, mode=0o700, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(dir=settings.cpanel_import_staging_dir, prefix=f"job-{job_id}-"))
    except Exception as exc:  # noqa: BLE001 - fatal: no staging space, nothing else is possible
        logger.exception("cpanel import job %d: could not create staging work dir", job_id)
        _update_job(job_id, status="failed", error=str(exc), progress_message="failed", completed_at=utcnow())
        return
    try:
        try:
            archive_path = _obtain_archive(job_id, source, source_ref, work_dir)
            _update_job(job_id, progress_message="extracting backup archive", source_ref=None)
            extract_dir = work_dir / "extracted"
            extract_dir.mkdir()
            _extract_archive(archive_path, extract_dir)
            root = _find_content_root(extract_dir)
            info = _parse_account_info(root)
        except Exception as exc:  # noqa: BLE001 - fatal: nothing else in this job is possible without a readable archive
            logger.exception("cpanel import job %d: failed to fetch/extract archive", job_id)
            _update_job(job_id, status="failed", error=str(exc), progress_message="failed", completed_at=utcnow())
            return
        finally:
            # The uploaded tarball itself (api/routers/cpanel_import.py spools
            # it to a plain tmp file forgehost-api can write and forgehostd,
            # as root, can read regardless of who wrote it) can hold real
            # customer data -- database dumps, mailbox contents -- and lives
            # outside work_dir (which is cleaned up separately below), so it
            # is deleted here explicitly the moment this daemon is done
            # reading it, success or failure, rather than left sitting in
            # /tmp indefinitely.
            if source == "upload":
                Path(source_ref).unlink(missing_ok=True)

        domains = _parse_domains(root, info)
        primary_domain = domains[0]["domain"] if domains and domains[0]["kind"] == "primary" else None

        _update_job(job_id, progress_message="creating account")
        account_password = generate_strong_password()
        try:
            handlers_account.create_account({"username": username, "password": account_password, "primary_domain": primary_domain})
            _append_result(
                job_id, "account", "ok",
                f"created account '{username}'" + (f" (source domain hint: {info.get('main_domain')})" if info.get("main_domain") else ""),
            )
        except Exception as exc:  # noqa: BLE001 - fatal: nothing else can attach to a nonexistent account
            logger.exception("cpanel import job %d: account creation failed", job_id)
            _update_job(job_id, status="failed", error=str(exc), progress_message="failed", completed_at=utcnow())
            return

        # Everything from here on is best-effort per item (goal: "fail
        # gracefully on unsupported items, import rest") -- _run_step
        # already catches each individual item's own exception, but this
        # outer guard exists so a bug in the orchestration code itself
        # (not any one item) still ends the job as "failed" instead of
        # leaving it stuck at "running" forever with no explanation.
        try:
            # DNS zones BEFORE domain.add: add_domain() only auto-creates an
            # A record when a Forgehost-managed zone already exists for the
            # domain (daemon/handlers_domain.py) -- importing the zone first
            # means every migrated domain's A record ends up correctly
            # re-pointed at *this* server the moment its Domain row is
            # added, not left pointing at the origin cPanel box's old IP.
            old_username = info.get("old_username")
            dnszones_dir = root / "dnszones"
            if dnszones_dir.is_dir():
                for domain_entry in domains:
                    zone_path = dnszones_dir / f"{domain_entry['domain']}.db"
                    if zone_path.is_file():
                        _run_step(job_id, f"dns:{domain_entry['domain']}", lambda d=domain_entry["domain"], p=zone_path: _import_dns_zone(username, d, p))

            for domain_entry in domains:
                _run_step(job_id, f"domain:{domain_entry['domain']}", lambda d=domain_entry: _add_domain_step(username, d))

            _run_step(job_id, "files", lambda: _copy_homedir(root, username))

            for domain_entry in domains:
                if domain_entry["kind"] == "primary":
                    continue
                _run_step(job_id, f"docroot-relocate:{domain_entry['domain']}", lambda d=domain_entry["domain"]: _relocate_docroot_step(root, username, d))

            for domain_entry in domains:
                _run_step(
                    job_id, f"docroot-perms:{domain_entry['domain']}",
                    lambda d=domain_entry: _reassert_docroot_perms_step(username, _domain_docroot(username, d["domain"])),
                )

            dumps = _parse_mysql_dumps(root)
            db_name_map: dict[str, str] = {}
            for dump_path in dumps:
                _run_step(job_id, f"database:{dump_path.stem}", lambda p=dump_path: _import_database_step(username, p, old_username, db_name_map))

            if dumps:
                for domain_entry in domains:
                    _run_step(job_id, f"wordpress:{domain_entry['domain']}", lambda d=domain_entry["domain"]: _wordpress_rewrite_step(username, d, db_name_map))

            certs = _parse_ssl_certs(root)
            imported_domains = {d["domain"] for d in domains}
            for domain_name, cert_path, key_path in certs:
                if domain_name not in imported_domains:
                    continue
                _run_step(job_id, f"ssl:{domain_name}", lambda dn=domain_name, cp=cert_path, kp=key_path: _import_ssl_cert(username, dn, cp, kp))

            mailboxes = _parse_mailboxes(root, [d["domain"] for d in domains])
            mail_domains_created: set[str] = set()
            for domain_name, local_part in mailboxes:
                if domain_name not in mail_domains_created:
                    try:
                        handlers_mail.create_mail_domain({"username": username, "domain": domain_name})
                    except Exception:  # noqa: BLE001
                        pass
                    mail_domains_created.add(domain_name)
                src = root / "homedir" / "mail" / domain_name / local_part
                _run_step(job_id, f"mailbox:{local_part}@{domain_name}", lambda d=domain_name, lp=local_part, s=src: _import_mailbox(d, lp, s))

            ftp_accounts = _parse_ftp_accounts(root, [d["domain"] for d in domains])
            for ftp_login, domain_name in ftp_accounts:
                _run_step(job_id, f"ftp:{ftp_login}", lambda fl=ftp_login, d=domain_name: _import_ftp_account(username, fl, d))

            cron_jobs = _parse_cron(root, old_username)
            for idx, (schedule, command) in enumerate(cron_jobs):
                _run_step(job_id, f"cron:{idx}", lambda s=schedule, c=command: _add_cron_step(username, s, c))
        except Exception as exc:  # noqa: BLE001 - see comment above: a bug here, not in one item, still must not hang the job
            logger.exception("cpanel import job %d: orchestration failed after account creation", job_id)
            _update_job(job_id, status="failed", error=str(exc), progress_message="failed", completed_at=utcnow())
            return

        _update_job(job_id, status="completed", progress_message="completed", completed_at=utcnow())
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _domain_docroot(username: str, domain: str) -> str:
    with write_session() as session:
        row = session.scalar(select(Domain).where(Domain.domain == domain))
        if row is None:
            raise CpanelImportError(f"domain '{domain}' has no Domain row")
        return row.docroot
