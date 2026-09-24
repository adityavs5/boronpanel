"""Phase 8 feature 5: email delivery log, scoped to an account's domains.

Parses Postfix's mail log (root-only, read here in borond) and returns the
last N delivery events (timestamp / from / to / status / reason) that touch one
of the account's own domains -- either the sender OR the recipient is on a
domain the account owns. An event between two OTHER accounts' domains is never
returned, so there is no cross-account leakage (the scoping filter is applied
here, in the daemon, before anything crosses the RPC boundary).

The parser is a pure function (`parse_maillog`) so it's unit-testable against
captured sample lines with no log file or root needed.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from typing import Iterable

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain, MailDomain
from shared.validation import validate_username

# postfix/<proc>[<pid>]: <QID>: <rest>   (syslog prefix captured loosely as ts)
_LINE_RE = re.compile(
    r"^(?P<ts>.+?)\s+\S+\s+postfix/(?P<proc>\w+)\[\d+\]:\s+(?P<qid>[0-9A-Za-z]+):\s+(?P<rest>.*)$"
)
_FROM_RE = re.compile(r"\bfrom=<(?P<addr>[^>]*)>")
_TO_RE = re.compile(r"\bto=<(?P<addr>[^>]*)>")
# status=<word> optionally followed by a "(...)" reason.
_STATUS_RE = re.compile(r"\bstatus=(?P<status>\S+)(?:\s+(?P<reason>.*))?$")
_UID_RE = re.compile(r"\buid=(?P<uid>\d+)\b")
_PHP_MAIL_RE = re.compile(
    r"mail\(\) on \[(?P<script>.+?):(?P<line>\d+)\]:\s+To:\s*(?P<to>.+?)(?:\s+--\s+Headers:|$)", re.I
)


def _domain_of(addr: str) -> str:
    """Domain part of an email address, lowercased. '' for the empty sender
    (<>, i.e. a bounce) or a malformed address."""
    if not addr or "@" not in addr:
        return ""
    return addr.rsplit("@", 1)[1].strip().lower()


def parse_maillog(lines: Iterable[str], account_domains: set[str], limit: int = 500,
                  uid_to_username: dict[int, str] | None = None) -> list[dict]:
    """Return delivery events (most recent first) where the sender or the
    recipient is on one of `account_domains`. Correlates each recipient/status
    line with its message's `from=` (logged earlier by qmgr) via the queue id."""
    account_domains = {d.lower() for d in account_domains}
    from_by_qid: dict[str, str] = {}
    user_by_qid: dict[str, str] = {}
    uid_to_username = uid_to_username or {}
    events: list[dict] = []

    for raw in lines:
        m = _LINE_RE.match(raw.rstrip("\n"))
        if not m:
            continue
        qid = m.group("qid")
        rest = m.group("rest")
        ts = m.group("ts").strip()

        fm = _FROM_RE.search(rest)
        uid = _UID_RE.search(rest)
        if uid:
            source_user = uid_to_username.get(int(uid.group("uid")))
            if source_user:
                user_by_qid[qid] = source_user
        if fm and "to=" not in rest:
            # A pure from-line (qmgr) -- remember it for this queue id.
            from_by_qid[qid] = fm.group("addr")

        sm = _STATUS_RE.search(rest)
        tm = _TO_RE.search(rest)
        if sm and tm:
            to_addr = tm.group("addr")
            # from= is usually on an earlier qmgr line; NOQUEUE rejects carry
            # from= inline on the same line.
            from_addr = fm.group("addr") if fm else from_by_qid.get(qid, "")
            from_dom = _domain_of(from_addr)
            to_dom = _domain_of(to_addr)
            if from_dom not in account_domains and to_dom not in account_domains:
                continue  # scoping: not this account's mail -> never returned
            reason = (sm.group("reason") or "").strip().strip("()")
            events.append({
                "timestamp": ts,
                "queue_id": qid if qid != "NOQUEUE" else None,
                "from": from_addr,
                "to": to_addr,
                "status": sm.group("status"),
                "reason": reason,
                "source_user": user_by_qid.get(qid),
            })

    # Most recent first; cap to the limit.
    events.reverse()
    return events[:limit]


def _tail_bytes(path: str, max_bytes: int) -> list[str]:
    """Read up to the last max_bytes of the file and return its lines (drops a
    possibly-partial first line so we never emit a half-parsed record)."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            f.readline()  # discard the partial line at the seek boundary
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    return text.splitlines()


def _account_domain_set(username: str) -> set[str]:
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        domains = set(session.scalars(select(Domain.domain).where(Domain.account_id == account.id)).all())
        mail_domains = set(session.scalars(select(MailDomain.domain).where(MailDomain.account_id == account.id)).all())
    return {d.lower() for d in (domains | mail_domains)}


def get_delivery_log(params: dict) -> dict:
    username = validate_username(params["username"])
    search = (params.get("search") or "").strip().lower()
    limit = int(params.get("limit") or settings.mail_delivery_log_max_entries)
    limit = max(1, min(limit, settings.mail_delivery_log_max_entries))

    account_domains = _account_domain_set(username)
    path = settings.mail_log_path
    if not account_domains or not os.path.exists(path):
        return {"entries": [], "count": 0, "scoped_domains": sorted(account_domains)}

    lines = _tail_bytes(path, settings.mail_log_scan_max_bytes)
    with write_session() as session:
        uid_map = {row.uid: row.username for row in session.scalars(select(Account)).all() if row.uid is not None}
    events = parse_maillog(lines, account_domains, limit=limit if not search else settings.mail_delivery_log_max_entries,
                           uid_to_username=uid_map)

    if search:
        events = [
            e for e in events
            if search in e["from"].lower() or search in e["to"].lower()
            or search in (e["status"] or "").lower() or search in (e["reason"] or "").lower()
        ][:limit]

    return {"entries": events, "count": len(events), "scoped_domains": sorted(account_domains)}


def parse_php_mail_log(lines: Iterable[str], username: str) -> list[dict]:
    events = []
    home = os.path.realpath(f"{settings.home_base}/{username}") + os.sep
    for raw in lines:
        match = _PHP_MAIL_RE.search(raw)
        if not match:
            continue
        script = os.path.realpath(match.group("script"))
        if not script.startswith(home):
            continue
        events.append({"username": username, "script": script, "line": int(match.group("line")),
                       "to": match.group("to").strip()[:320]})
    return events


def get_admin_stats(params: dict | None = None) -> dict:
    """On-demand bounded mail overview; no background database or realtime load."""
    with write_session() as session:
        accounts = session.scalars(select(Account).where(Account.status != "terminated")).all()
        account_by_id = {row.id: row.username for row in accounts}
        uid_map = {row.uid: row.username for row in accounts if row.uid is not None}
        domain_owner = {
            domain.lower(): account_by_id[account_id]
            for domain, account_id in session.execute(select(Domain.domain, Domain.account_id))
            if account_id in account_by_id
        }
        domain_owner.update({
            domain.lower(): account_by_id[account_id]
            for domain, account_id in session.execute(select(MailDomain.domain, MailDomain.account_id))
            if account_id in account_by_id
        })

    events = []
    if domain_owner and os.path.exists(settings.mail_log_path):
        events = parse_maillog(
            _tail_bytes(settings.mail_log_path, settings.mail_log_scan_max_bytes),
            set(domain_owner), limit=settings.mail_delivery_log_max_entries,
            uid_to_username=uid_map,
        )
    by_account = Counter()
    by_sender = Counter()
    by_domain = Counter()
    by_status = Counter()
    for event in events:
        sender_domain = _domain_of(event["from"])
        username = event.get("source_user") or domain_owner.get(sender_domain) or domain_owner.get(_domain_of(event["to"]))
        event["account"] = username
        if username:
            by_account[username] += 1
        if event["from"]:
            by_sender[event["from"]] += 1
        if sender_domain:
            by_domain[sender_domain] += 1
        by_status[event["status"]] += 1

    script_counts = Counter()
    for account in accounts:
        path = f"{settings.home_base}/{account.username}/logs/php-mail.log"
        if not os.path.exists(path):
            continue
        for item in parse_php_mail_log(_tail_bytes(path, min(settings.mail_log_scan_max_bytes, 2 * 1024 * 1024)), account.username):
            script_counts[(item["username"], item["script"], item["to"])] += 1

    ranked = lambda counter, key_names: [dict(zip(key_names, key if isinstance(key, tuple) else (key,)), count=count)
                                         for key, count in counter.most_common(50)]
    return {
        "summary": {"total": len(events), "sent": by_status.get("sent", 0),
                    "bounced": by_status.get("bounced", 0), "deferred": by_status.get("deferred", 0),
                    "rejected": by_status.get("rejected", 0)},
        "by_account": ranked(by_account, ("account",)),
        "by_sender": ranked(by_sender, ("sender",)),
        "by_domain": ranked(by_domain, ("domain",)),
        "scripts": ranked(script_counts, ("account", "script", "to")),
        "entries": events,
    }
