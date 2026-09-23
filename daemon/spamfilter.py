"""SpamAssassin integration (Phase 4 feature 1), extended by the
missing-features batch (goal feature 5) with per-mailbox blacklist/whitelist.

Architecture:

- **spamd runs once, server-wide**, scanning every inbound message
  unconditionally (Postfix's `content_filter`, restricted to the `smtp
  inet` service only -- inbound mail from the internet, never the
  `submission` service customers send *through*, which would be a strange
  thing to spam-scan). Per-domain "enable/disable" and "custom threshold"
  are **not** implemented as separate scanning paths -- they're both just
  SpamAssassin's own `required_score`, looked up per recipient via spamd's
  `--virtual-config-dir` (built for exactly this "no real Unix user"
  scenario, the same problem this project's virtual mailboxes already
  posed for autoresponders in Phase 3 feature 4). "Disabled" sets
  `required_score` to a value no real message will ever reach -- simpler
  and more robust than trying to route disabled domains around the
  content filter at the Postfix transport level, and it still means a
  disabled domain's mail is scanned (so `X-Spam-Score` is still visible
  for the curious) but never *acted on*.
- **The Junk-filing decision itself lives in ONE global Dovecot Sieve
  script** (`sieve_before`, run before any mailbox's own personal script,
  Phase 3 feature 4's autoresponder Sieve), not duplicated per mailbox --
  it only ever checks `X-Spam-Flag: YES`, a header spamd itself sets
  per-message based on the per-recipient threshold above. One rule,
  identical for every mailbox on the server; the interesting per-account
  behavior already happened one layer up, at scan time.
- Settings are **per mail domain**, not per mailbox -- matches this
  project's existing domain-scoped email-feature API shape (Phase 3
  feature 4: catchall/forwarders/autoresponders are all per-domain).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select

from shared.db import write_session
from shared.models import MailDomain, SpamFilterEntry, SpamGlobalSettings
from shared.validation import (
    ValidationError,
    validate_domain,
    validate_mailbox_local_part,
    validate_spam_filter_kind,
    validate_spam_filter_pattern,
    validate_spam_threshold,
)

from daemon.configtx import ConfigWriter, StepResult
from daemon.procutil import run

logger = logging.getLogger("borond.spamfilter")

VIRTUAL_CONFIG_BASE = "/etc/boron/spamassassin"
SPAMD_DEFAULTS_PATH = "/etc/default/spamd"
LOCAL_CF_PATH = "/etc/spamassassin/local.cf"
MASTER_CF_PATH = "/etc/postfix/master.cf"
POSTFIX_DIR = "/etc/postfix"
SIEVE_PLUGIN_CONF_PATH = "/etc/dovecot/conf.d/90-sieve.conf"
GLOBAL_SIEVE_DIR = "/etc/dovecot/sieve-global"
GLOBAL_SIEVE_PATH = f"{GLOBAL_SIEVE_DIR}/spam-to-junk.sieve"
SIEVEC_BIN = "/usr/bin/sievec"

# A message no real SpamAssassin score will ever reach (default rule set
# tops out well under 100) -- "disabled" without a separate scanning path,
# see module docstring.
DISABLED_THRESHOLD = 1000.0

BACKUP_DIR = "/var/lib/boron/backups"


class SpamFilterError(Exception):
    pass


# --- per-domain settings (no service reload needed -- spamd reads a
# virtual-config-dir prefs file fresh per scan, same "no reload for
# routine mail CRUD" property Phase e's SQL-backed mailboxes already
# have) -----------------------------------------------------------------


def _user_prefs_path(domain: str) -> Path:
    return Path(VIRTUAL_CONFIG_BASE) / domain / "user_prefs"


def apply_domain_spam_settings(domain: str, enabled: bool, threshold: float | None) -> None:
    """threshold=None (with enabled=True) means "use the server-wide admin
    default" -- no per-domain file is written at all in that case (and any
    previous override is removed), so a later change to the global default
    is picked up automatically rather than needing every domain using it
    rewritten."""
    domain = validate_domain(domain)
    path = _user_prefs_path(domain)
    if enabled and threshold is None:
        path.unlink(missing_ok=True)
        return

    score = threshold if enabled else DISABLED_THRESHOLD
    content = f"required_score {score}\n"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    tmp_path = path.with_suffix(f".tmp.{os.getpid()}")
    tmp_path.write_text(content)
    os.chmod(tmp_path, 0o644)
    os.replace(tmp_path, path)


def remove_domain_spam_settings(domain: str) -> None:
    """TERMINATE_HOOKS-adjacent cleanup (called from handlers_domain's
    domain-removal path, mirroring redirects/DNS-zone per-domain cleanup)
    -- idempotent, safe even if the domain never had an override."""
    shutil.rmtree(Path(VIRTUAL_CONFIG_BASE) / domain, ignore_errors=True)


# --- global admin default -------------------------------------------------


def get_global_default_threshold() -> float:
    with write_session() as session:
        row = session.scalar(select(SpamGlobalSettings).where(SpamGlobalSettings.id == 1))
        return row.default_threshold if row else 5.0


def set_global_default_threshold(threshold: float) -> dict:
    threshold = validate_spam_threshold(threshold)
    old_threshold = get_global_default_threshold()

    _write_local_cf(threshold)
    try:
        with write_session() as session:
            row = session.scalar(select(SpamGlobalSettings).where(SpamGlobalSettings.id == 1))
            if row is None:
                row = SpamGlobalSettings(id=1, default_threshold=threshold)
                session.add(row)
            else:
                row.default_threshold = threshold
            session.flush()
            result_threshold = row.default_threshold
    except Exception:
        try:
            _write_local_cf(old_threshold)
        except Exception:
            logger.exception("failed to restore SpamAssassin global default after DB write failure")
        raise

    return {"default_threshold": result_threshold}


def _write_local_cf(default_threshold: float) -> None:
    content = (
        "# Managed by Boron (daemon/spamfilter.py) -- do not hand-edit,\n"
        "# changes are overwritten the next time the admin default changes.\n"
        f"required_score {default_threshold}\n"
        "rewrite_header Subject [SPAM]\n"
        "report_safe 0\n"
    )
    writer = ConfigWriter(
        target_path=LOCAL_CF_PATH,
        validate=_validate_local_cf_candidate,
        reload=lambda: StepResult(True, "no service reload required for linted local.cf"),
        verify=_lint_live_spamassassin_config,
        backup_dir=BACKUP_DIR,
        subsystem="spamassassin-localcf",
    )
    result = writer.apply(content)
    if not result.ok:
        raise SpamFilterError(f"SpamAssassin local.cf update failed: {result.summary()}")


def _spamassassin_lint_result(args: list[str]) -> StepResult:
    result = run(args, timeout=30)
    if result.ok:
        return StepResult(True)
    return StepResult(False, result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}")


def _validate_local_cf_candidate(candidate: Path) -> StepResult:
    target = Path(LOCAL_CF_PATH)
    with tempfile.TemporaryDirectory(prefix="boron-spamassassin-") as tmpdir:
        site_dir = Path(tmpdir)
        copied_from: set[Path] = set()
        for existing_dir in (Path("/etc/spamassassin"), target.parent):
            if not existing_dir.is_dir() or existing_dir in copied_from:
                continue
            copied_from.add(existing_dir)
            for child in existing_dir.iterdir():
                if child.is_file() and child.suffix in {".cf", ".pre"}:
                    shutil.copy2(child, site_dir / child.name)
        shutil.copy2(candidate, site_dir / target.name)
        return _spamassassin_lint_result(["spamassassin", "--lint", "-L", "--siteconfigpath", str(site_dir)])


def _lint_live_spamassassin_config() -> StepResult:
    return _spamassassin_lint_result(["spamassassin", "--lint", "-L"])


# --- one-time global bootstrap (system.bootstrap_spamassassin RPC op,
# mirroring daemon/ols.py's bootstrap_baseline / daemon/pma.py's
# bootstrap_pma -- run once, deliberately, not automatically on daemon
# startup) -------------------------------------------------------------


def bootstrap_spamassassin() -> dict:
    default_threshold = get_global_default_threshold()
    _write_local_cf(default_threshold)
    _write_spamd_defaults()
    _restart_spamd()
    postfix_result = _ensure_postfix_wiring()
    sieve_result = _ensure_dovecot_sieve_wiring()
    return {
        "status": "ok",
        "postfix": postfix_result.summary() if postfix_result else "already configured",
        "dovecot": sieve_result.summary() if sieve_result else "already configured",
    }


def _write_spamd_defaults() -> None:
    """--virtual-config-dir is spamd's own documented mechanism for
    per-recipient config with no real Unix account backing the recipient
    (spamd manpage: "needs -x") -- exactly this project's virtual-mailbox
    problem, same shape as Phase 3 feature 4's autoresponder Sieve
    solving the equivalent problem for vacation replies. %d is the
    recipient's domain (split from -u's argument by spamd itself); this
    project's settings are per-domain, so %l (local-part) is deliberately
    not part of the pattern."""
    content = (
        "# Managed by Boron (daemon/spamfilter.py).\n"
        'OPTIONS="--create-prefs --max-children 5 --helper-home-dir '
        f'--virtual-config-dir={VIRTUAL_CONFIG_BASE}/%d -x -u debian-spamd -H"\n'
    )
    path = Path(SPAMD_DEFAULTS_PATH)
    tmp_path = path.with_suffix(f".tmp.{os.getpid()}")
    tmp_path.write_text(content)
    os.replace(tmp_path, path)


def _restart_spamd() -> None:
    run(["systemctl", "enable", "spamd"], timeout=15)
    result = run(["systemctl", "restart", "spamd"], timeout=30)
    if not result.ok:
        raise SpamFilterError(f"failed to restart spamd: {result.stderr.strip() or result.stdout.strip()}")


# --- Postfix: one new pipe service ("scan") + a content_filter override
# on the inbound smtp service only (never `submission` -- that's mail
# customers send *through* Boron, not mail arriving *at* it, and
# scanning your own customers' outbound mail for spam is a different
# feature this goal doesn't ask for). Content_filter is deliberately a
# per-service master.cf override, not a main.cf global -- main.cf's
# content_filter would also apply to the *reinjected* mail spamc's own
# `-e sendmail` hands back to Postfix, looping the scan forever; scoping
# it to the `smtp inet` service line only means the reinjection (which
# re-enters via `sendmail`/pickup, a different path entirely) is never
# rescanned. -----------------------------------------------------------


SCAN_SERVICE_BLOCK = (
    "scan      unix  -       n       n       -       10      pipe\n"
    "  user=debian-spamd argv=/usr/bin/spamc -f -u ${recipient} -e /usr/sbin/sendmail -oi -f ${sender} ${recipient}\n"
)
CONTENT_FILTER_OVERRIDE = "  -o content_filter=scan:\n"


def _master_cf_needs_scan_service(content: str) -> bool:
    return "\nscan " not in content and not content.startswith("scan ")


def _render_master_cf(content: str) -> str:
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    added_override = False
    for i, line in enumerate(lines):
        out.append(line)
        if line.startswith("smtp") and "inet" in line.split() and not added_override:
            # only the *first* whitespace-delimited field must be exactly
            # "smtp" (master.cf's service-name column) -- "smtp unix" (the
            # outbound relay transport, further down the file) must not
            # match, confirmed by reading the stock file's own layout
            # before writing this check.
            if line.split()[0] == "smtp":
                next_line = lines[i + 1] if i + 1 < len(lines) else ""
                if "content_filter=scan:" not in next_line:
                    out.append(CONTENT_FILTER_OVERRIDE)
                added_override = True
    if not added_override:
        raise SpamFilterError("master.cf has no 'smtp inet' service line to attach content_filter to")
    rendered = "".join(out)
    if _master_cf_needs_scan_service(rendered):
        if not rendered.endswith("\n"):
            rendered += "\n"
        rendered += "\n" + SCAN_SERVICE_BLOCK
    return rendered


def _postfix_validate(tmp_path: Path) -> StepResult:
    """ARCHITECTURE.md SS7: postfix check against a scratch copy of the
    config dir (postfix's own -c flag), never against the live tree."""
    with tempfile.TemporaryDirectory() as scratch:
        for name in os.listdir(POSTFIX_DIR):
            src = Path(POSTFIX_DIR) / name
            if src.is_file():
                shutil.copy2(src, Path(scratch) / name)
        shutil.copy2(tmp_path, Path(scratch) / "master.cf")
        result = run(["postfix", "-c", scratch, "check"], timeout=20)
        if not result.ok:
            return StepResult(False, result.stderr.strip() or result.stdout.strip())
        return StepResult(True)


def _postfix_reload() -> StepResult:
    result = run(["postfix", "reload"], timeout=20)
    if not result.ok:
        return StepResult(False, result.stderr.strip() or result.stdout.strip())
    return StepResult(True)


def _postfix_verify() -> StepResult:
    result = run(["systemctl", "is-active", "postfix"], timeout=10)
    if result.stdout.strip() != "active":
        return StepResult(False, f"postfix not active after reload: {result.stdout.strip()}")
    return StepResult(True)


def _ensure_postfix_wiring():
    current = Path(MASTER_CF_PATH).read_text()
    if "content_filter=scan:" in current and not _master_cf_needs_scan_service(current):
        return None  # already configured, idempotent no-op

    new_content = _render_master_cf(current)
    writer = ConfigWriter(
        target_path=MASTER_CF_PATH,
        validate=_postfix_validate,
        reload=_postfix_reload,
        verify=_postfix_verify,
        backup_dir=BACKUP_DIR,
        subsystem="postfix-spamfilter",
    )
    result = writer.apply(new_content)
    if not result.ok:
        raise SpamFilterError(f"Postfix content_filter wiring failed: {result.summary()}")
    return result


# --- Dovecot: one global sieve_before script -----------------------------

# Phase 4 feature 1's original static tail: files spam-flagged mail into
# Junk. Missing-features batch, goal feature 5 (per-mailbox spam filters)
# PREPENDS generated per-mailbox blacklist/whitelist blocks ahead of this
# same rule (build_global_sieve_source below) rather than introducing a
# second sieve_before script or touching SpamAssassin's own per-DOMAIN
# virtual-config-dir wiring above -- see this module's design note in
# docs/CHECKPOINT-imapsync-spamfilters.md for why: whitelist has to run
# BEFORE the spam-flag decision is acted on (an already-flagged message that
# hits this rule's own `stop` never reaches any later script, including a
# per-mailbox PERSONAL script -- so a personal-script-based whitelist would
# be structurally too late), and blacklist needs a real SMTP-time-visible
# reject, which Sieve's `reject` action produces via a DSN back through the
# Postfix->Dovecot LMTP hop this project already uses for delivery. Both are
# therefore generated INTO this same global, envelope-recipient-scoped
# script instead.
SPAM_JUNK_RULE = (
    'if header :contains "X-Spam-Flag" "YES" {\n'
    '    fileinto :create "Junk";\n'
    "    stop;\n"
    "}\n"
)


def _sieve_string_literal(value: str) -> str:
    """Sieve string literals use the same backslash/double-quote escaping
    as C -- values here are already validated (validate_spam_filter_pattern:
    a syntactically valid email address or bare domain, shared/validation.py)
    so this is defense in depth, not the primary injection guard."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _sieve_block_for_entry(entry: SpamFilterEntry) -> str:
    recipient = _sieve_string_literal(f"{entry.local_part}@{entry.domain}")
    is_domain_pattern = "@" not in entry.pattern
    if is_domain_pattern:
        sender_test = f'address :domain :is "from" "{_sieve_string_literal(entry.pattern)}"'
    else:
        sender_test = f'address :all :is "from" "{_sieve_string_literal(entry.pattern)}"'

    if entry.kind == "whitelist":
        # keep+stop bypasses the spam-to-Junk rule below entirely for this
        # (recipient, sender) pair -- delivered to the inbox regardless of
        # whatever X-Spam-Flag SpamAssassin already set on the message.
        action = "keep;\n    stop;"
    else:
        action = 'reject "This message has been blocked by the recipient\'s spam filter.";\n    stop;'
    return (
        f'if allof(envelope :is "to" "{recipient}", {sender_test}) {{\n'
        f"    {action}\n"
        f"}}\n"
    )


def build_global_sieve_source() -> str:
    """The full global sieve_before script: one generated block per
    SpamFilterEntry row (server-wide -- envelope :is "to" scopes each block
    to its own mailbox, so one shared script is correct and simpler than a
    per-mailbox file, matching the goal's own single-global-rule precedent
    this function extends) followed by the original static Junk-filing
    rule, unchanged in position and behavior."""
    with write_session() as session:
        entries = session.scalars(select(SpamFilterEntry).order_by(SpamFilterEntry.id)).all()
        blocks = [_sieve_block_for_entry(e) for e in entries]

    needs_reject = any(e.kind == "blacklist" for e in entries)
    requires = ["fileinto", "mailbox", "envelope"] + (["reject"] if needs_reject else [])
    header = f'require {json.dumps(requires)};\n\n'
    return header + "\n".join(blocks) + ("\n" if blocks else "") + SPAM_JUNK_RULE


def _validate_sieve(content: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sieve", delete=False) as f:
        f.write(content)
        tmp_path = Path(f.name)
    try:
        result = run([SIEVEC_BIN, str(tmp_path)], timeout=15)
        if not result.ok:
            raise SpamFilterError(f"global Sieve script failed validation: {result.stderr.strip() or result.stdout.strip()}")
    finally:
        tmp_path.unlink(missing_ok=True)
        tmp_path.with_suffix(".svbin").unlink(missing_ok=True)


def _install_global_sieve_script(content: str | None = None) -> None:
    content = build_global_sieve_source() if content is None else content
    def validate(path: Path) -> StepResult:
        try:
            _validate_sieve(path.read_text())
        except SpamFilterError as exc:
            return StepResult(False, str(exc))
        return StepResult(True)

    def compile_and_reload() -> StepResult:
        result = run([SIEVEC_BIN, GLOBAL_SIEVE_PATH], timeout=15)
        if not result.ok:
            return StepResult(False, "global Sieve compile failed")
        return _dovecot_reload()

    writer = ConfigWriter(
        target_path=GLOBAL_SIEVE_PATH,
        validate=validate,
        reload=compile_and_reload,
        verify=_dovecot_verify,
        backup_dir=BACKUP_DIR,
        subsystem="global-sieve",
    )
    result = writer.apply(content)
    if not result.ok:
        raise SpamFilterError(f"global Sieve update failed: {result.summary()}")


def _dovecot_validate(tmp_path: Path) -> StepResult:
    result = run(["doveconf", "-n", "-c", str(tmp_path)], timeout=15)
    if not result.ok:
        return StepResult(False, result.stderr.strip() or result.stdout.strip())
    return StepResult(True)


def _dovecot_reload() -> StepResult:
    result = run(["systemctl", "reload", "dovecot"], timeout=20)
    if not result.ok:
        return StepResult(False, result.stderr.strip() or result.stdout.strip())
    return StepResult(True)


def _dovecot_verify() -> StepResult:
    result = run(["systemctl", "is-active", "dovecot"], timeout=10)
    if result.stdout.strip() != "active":
        return StepResult(False, f"dovecot not active after reload: {result.stdout.strip()}")
    return StepResult(True)


def _ensure_dovecot_sieve_wiring():
    current = Path(SIEVE_PLUGIN_CONF_PATH).read_text()
    if f"sieve_before = {GLOBAL_SIEVE_PATH}" in current:
        _install_global_sieve_script()
        return None  # already wired, idempotent no-op (script content still refreshed)

    _install_global_sieve_script()
    marker = "  sieve = file:~/sieve;active=~/.dovecot.sieve\n"
    if marker not in current:
        raise SpamFilterError("90-sieve.conf's expected 'sieve =' line not found -- refusing to guess where to insert sieve_before")
    new_content = current.replace(marker, marker + f"  sieve_before = {GLOBAL_SIEVE_PATH}\n", 1)

    writer = ConfigWriter(
        target_path=SIEVE_PLUGIN_CONF_PATH,
        validate=_dovecot_validate,
        reload=_dovecot_reload,
        verify=_dovecot_verify,
        backup_dir=BACKUP_DIR,
        subsystem="dovecot-spamfilter",
    )
    result = writer.apply(new_content)
    if not result.ok:
        raise SpamFilterError(f"Dovecot sieve_before wiring failed: {result.summary()}")
    return result


# --- Missing-features batch, goal feature 5: per-mailbox blacklist/whitelist
# CRUD (daemon/server.py's OP_TABLE) --------------------------------------

MAX_SPAM_FILTER_ENTRIES_PER_MAILBOX = 500
MAX_IMPORT_LINES = 500


def _entry_to_dict(row: SpamFilterEntry) -> dict:
    return {
        "id": row.id,
        "domain": row.domain,
        "local_part": row.local_part,
        "kind": row.kind,
        "pattern": row.pattern,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _refresh_global_sieve() -> None:
    """Re-render + reinstall the global sieve_before script from current DB
    state and reload dovecot -- same validate/apply/reload/verify pipeline
    _ensure_dovecot_sieve_wiring already uses, just re-run on every entry
    change instead of only at bootstrap time."""
    _install_global_sieve_script()


def list_entries(params: dict) -> dict:
    domain = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    with write_session() as session:
        rows = session.scalars(
            select(SpamFilterEntry).where(SpamFilterEntry.domain == domain, SpamFilterEntry.local_part == local_part)
        ).all()
        return {
            "domain": domain,
            "local_part": local_part,
            "entries": [_entry_to_dict(r) for r in rows],
        }


def add_entry(params: dict) -> dict:
    domain = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    kind = validate_spam_filter_kind(params["kind"])
    pattern = validate_spam_filter_pattern(params["pattern"])

    with write_session() as session:
        count = len(session.scalars(
            select(SpamFilterEntry.id).where(SpamFilterEntry.domain == domain, SpamFilterEntry.local_part == local_part)
        ).all())
        if count >= MAX_SPAM_FILTER_ENTRIES_PER_MAILBOX:
            raise SpamFilterError(f"mailbox already has the maximum of {MAX_SPAM_FILTER_ENTRIES_PER_MAILBOX} spam filter entries")
        existing = session.scalar(
            select(SpamFilterEntry).where(
                SpamFilterEntry.domain == domain,
                SpamFilterEntry.local_part == local_part,
                SpamFilterEntry.kind == kind,
                SpamFilterEntry.pattern == pattern,
            )
        )
        if existing is not None:
            result = _entry_to_dict(existing)
        else:
            row = SpamFilterEntry(domain=domain, local_part=local_part, kind=kind, pattern=pattern)
            session.add(row)
            session.flush()
            result = _entry_to_dict(row)

    _refresh_global_sieve()
    return result


def delete_entry(params: dict) -> dict:
    domain = validate_domain(params["domain"])
    with write_session() as session:
        row = session.get(SpamFilterEntry, int(params["id"]))
        # Cross-account IDOR guard: id is a small sequential int, not a
        # capability. `domain` was already authorized against the caller's
        # own account at the API layer (require_domain_access) -- checking
        # the row's domain matches it is what actually ties "may delete
        # this id" back to "owns this domain", so a different account can't
        # delete another mailbox's entry by guessing an id. Same "not
        # found" on mismatch as a genuinely missing row, not a
        # distinguishable permission error.
        if row is None or row.domain != domain:
            raise RuntimeError(f"spam filter entry {params['id']} not found")
        deleted = _entry_to_dict(row)
        session.delete(row)

    _refresh_global_sieve()
    return {**deleted, "status": "deleted"}


def import_entries(params: dict) -> dict:
    """goal: "import from text list" -- one pattern per line, blank lines
    and '#'-prefixed comments ignored. All entries in the batch share the
    same (domain, local_part, kind), matching the goal's per-mailbox,
    per-kind import UX (one "import blacklist" / "import whitelist" action
    per mailbox)."""
    domain = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    kind = validate_spam_filter_kind(params["kind"])
    raw_lines = [line.strip() for line in str(params.get("text", "")).splitlines()]
    lines = [line for line in raw_lines if line and not line.startswith("#")]
    if len(lines) > MAX_IMPORT_LINES:
        raise SpamFilterError(f"import list must have at most {MAX_IMPORT_LINES} entries")

    added: list[dict] = []
    errors: list[dict] = []
    with write_session() as session:
        existing_count = len(session.scalars(
            select(SpamFilterEntry.id).where(SpamFilterEntry.domain == domain, SpamFilterEntry.local_part == local_part)
        ).all())
        for line in lines:
            try:
                pattern = validate_spam_filter_pattern(line)
            except ValidationError as exc:
                errors.append({"line": line, "error": str(exc)})
                continue
            if existing_count + len(added) >= MAX_SPAM_FILTER_ENTRIES_PER_MAILBOX:
                errors.append({"line": line, "error": "mailbox spam filter entry limit reached"})
                continue
            existing = session.scalar(
                select(SpamFilterEntry).where(
                    SpamFilterEntry.domain == domain,
                    SpamFilterEntry.local_part == local_part,
                    SpamFilterEntry.kind == kind,
                    SpamFilterEntry.pattern == pattern,
                )
            )
            if existing is not None:
                continue  # already present -- not an error, just a no-op
            row = SpamFilterEntry(domain=domain, local_part=local_part, kind=kind, pattern=pattern)
            session.add(row)
            session.flush()
            added.append(_entry_to_dict(row))

    if added:
        _refresh_global_sieve()
    return {"domain": domain, "local_part": local_part, "kind": kind, "added": added, "errors": errors}


def delete_entries_for_mailbox(domain: str, local_part: str) -> None:
    """Called from handlers_mail.delete_mailbox -- this project's manual-
    cascade convention (no DB-level ON DELETE CASCADE anywhere in this
    schema)."""
    with write_session() as session:
        rows = session.scalars(
            select(SpamFilterEntry).where(SpamFilterEntry.domain == domain, SpamFilterEntry.local_part == local_part)
        ).all()
        any_deleted = bool(rows)
        for row in rows:
            session.delete(row)
    if any_deleted:
        _refresh_global_sieve()
