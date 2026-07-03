"""Phase 5 feature 3: Postfix mail queue viewer.

Parses `mailq`/`postqueue -p` output (never Postfix's own internal queue
file format directly -- the CLI tool is the stable, documented interface;
the on-disk format is not) and drives `postqueue`/`postsuper` for
flush/delete actions, the same two tools any operator would use by hand.
"""
from __future__ import annotations

import datetime as dt
import re

from shared.validation import ValidationError

from daemon.procutil import run

# One entry's header line, e.g.:
#   4C87949727*     380 Fri Jul  3 17:32:11  root@cpanel-test.local
# Queue ID flag: '*' = active (currently being delivered), '!' = held for
# immediate delivery -- both confirmed against this server's own real
# `mailq` output before writing this regex, not assumed from Postfix's
# documentation alone.
_HEADER_RE = re.compile(
    r"^(?P<qid>[A-Za-z0-9]+)(?P<flag>[*!])?\s+"
    r"(?P<size>\d+)\s+"
    r"(?P<dow>\w{3})\s+(?P<mon>\w{3})\s+(?P<day>\d{1,2})\s+(?P<time>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<sender>\S+)\s*$"
)

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_arrival(mon: str, day: str, time_str: str) -> dt.datetime:
    """mailq's arrival time has no year -- inferred as "this year, unless
    that would put it in the future" (e.g. a message that arrived last
    December, inspected in January)."""
    now = dt.datetime.now(dt.timezone.utc)
    hour, minute, second = (int(p) for p in time_str.split(":"))
    candidate = dt.datetime(now.year, _MONTHS.get(mon, 1), int(day), hour, minute, second, tzinfo=dt.timezone.utc)
    if candidate > now + dt.timedelta(days=1):
        candidate = candidate.replace(year=now.year - 1)
    return candidate


def _parse_mailq(text: str) -> list[dict]:
    lines = text.splitlines()
    entries = []
    current = None
    for line in lines:
        if not line.strip():
            if current:
                entries.append(current)
                current = None
            continue
        if line.startswith("-Queue ID-") or line.startswith("--"):
            continue  # header / footer summary line
        header_match = _HEADER_RE.match(line)
        if header_match:
            if current:
                entries.append(current)
            arrival = _parse_arrival(header_match.group("mon"), header_match.group("day"), header_match.group("time"))
            current = {
                "queue_id": header_match.group("qid"),
                "flag": header_match.group("flag") or "",
                "size": int(header_match.group("size")),
                "arrival": arrival,
                "sender": header_match.group("sender"),
                "recipients": [],
                "reason": None,
            }
            continue
        stripped = line.strip()
        if current is None:
            continue  # stray line before any header seen -- ignore
        if stripped.startswith("(") :
            reason = stripped[1:]
            if reason.endswith(")"):
                reason = reason[:-1]
            current["reason"] = reason
        else:
            current["recipients"].append(stripped)
    if current:
        entries.append(current)
    return entries


def _status_for(flag: str) -> str:
    return {"*": "active", "!": "held"}.get(flag, "deferred")


def list_queue(params: dict) -> dict:
    search = (params.get("search") or "").strip().lower()
    result = run(["mailq"], timeout=15)
    entries = _parse_mailq(result.stdout)
    now = dt.datetime.now(dt.timezone.utc)

    rows = []
    for entry in entries:
        recipients = entry["recipients"] or [""]
        for recipient in recipients:
            if search and search not in entry["sender"].lower() and search not in recipient.lower():
                continue
            rows.append(
                {
                    "queue_id": entry["queue_id"],
                    "status": _status_for(entry["flag"]),
                    "sender": entry["sender"],
                    "recipient": recipient,
                    "size": entry["size"],
                    "arrival": entry["arrival"].isoformat(),
                    "age_seconds": int((now - entry["arrival"]).total_seconds()),
                    "defer_reason": entry["reason"],
                }
            )
    return {"entries": rows, "count": len(rows)}


_QUEUE_ID_RE = re.compile(r"\A[A-Za-z0-9]{6,20}\Z")


def _validate_queue_id(queue_id: str) -> str:
    if not isinstance(queue_id, str) or not _QUEUE_ID_RE.match(queue_id):
        raise ValidationError(f"'{queue_id}' is not a valid Postfix queue ID")
    return queue_id


def flush_message(params: dict) -> dict:
    queue_id = _validate_queue_id(params["queue_id"])
    result = run(["postqueue", "-i", queue_id], timeout=30)
    if not result.ok:
        raise RuntimeError(f"postqueue -i {queue_id} failed: {result.stderr.strip() or result.stdout.strip()}")
    return {"queue_id": queue_id, "status": "flush_requested"}


def flush_all(params: dict) -> dict:
    result = run(["postqueue", "-f"], timeout=60)
    if not result.ok:
        raise RuntimeError(f"postqueue -f failed: {result.stderr.strip() or result.stdout.strip()}")
    return {"status": "flush_requested"}


def delete_message(params: dict) -> dict:
    queue_id = _validate_queue_id(params["queue_id"])
    result = run(["postsuper", "-d", queue_id], timeout=30)
    if not result.ok:
        raise RuntimeError(f"postsuper -d {queue_id} failed: {result.stderr.strip() or result.stdout.strip()}")
    return {"queue_id": queue_id, "status": "deleted"}


def delete_all(params: dict) -> dict:
    result = run(["postsuper", "-d", "ALL"], timeout=60)
    if not result.ok:
        raise RuntimeError(f"postsuper -d ALL failed: {result.stderr.strip() or result.stdout.strip()}")
    return {"status": "deleted_all"}
