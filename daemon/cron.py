"""Per-account crontab management (Phase 2 feature 2).

The real crontab is the only source of truth -- no shadow table in the
panel DB to drift out of sync with it. Jobs are identified by a marker
comment line Boron writes immediately above each job it manages
(`# boron:id=<uuid> label=<label>`), so add/edit/delete can target one
job without disturbing any other line in the account's crontab (including
lines the account added some other way, though there's no SSH access in
v1 to actually do that -- still handled correctly rather than assumed
away).

Runs as root (borond), using `crontab -u <username>` -- this executes
the job as *that* Linux user, never root (`crontab -u` only changes whose
table is read/written; the cron daemon itself still runs each user's jobs
under that user's own uid, same as it always has since before this project
existed).
"""
from __future__ import annotations

import re
import uuid

from croniter import croniter

from shared.validation import validate_cron_mailto

from daemon.procutil import run

MARKER_RE = re.compile(r"^#\s*boron:id=([0-9a-f-]{36})(?:\s+label=(.*))?$")
MAILTO_RE = re.compile(r"^MAILTO\s*=\s*(.*)$")
MAX_COMMAND_LEN = 1000
MAX_LABEL_LEN = 200


class CronError(Exception):
    pass


def _validate_schedule(schedule: str) -> str:
    schedule = schedule.strip()
    if not croniter.is_valid(schedule):
        raise CronError(f"'{schedule}' is not a valid 5-field cron expression")
    return schedule


def _validate_command(command: str) -> str:
    command = command.strip()
    if not command:
        raise CronError("command must not be empty")
    if "\n" in command or "\r" in command:
        # Not a privilege-escalation risk (it's the account's own
        # crontab -- they could always add arbitrary lines themselves),
        # but a newline here would let one "add_job" call silently inject
        # a second, unvalidated cron line into the file. Reject rather
        # than corrupt the format.
        raise CronError("command must not contain newlines")
    if len(command) > MAX_COMMAND_LEN:
        raise CronError(f"command exceeds {MAX_COMMAND_LEN} characters")
    return command


def _validate_label(label: str) -> str:
    label = (label or "").strip()
    if "\n" in label or "\r" in label:
        raise CronError("label must not contain newlines")
    if len(label) > MAX_LABEL_LEN:
        raise CronError(f"label exceeds {MAX_LABEL_LEN} characters")
    return label


def _read_raw(username: str) -> list[str]:
    result = run(["crontab", "-u", username, "-l"], timeout=10)
    if not result.ok:
        # "no crontab for <user>" on stderr is the normal empty-crontab
        # case, not a real error -- anything else genuinely failed.
        if "no crontab for" in (result.stderr or "").lower():
            return []
        raise CronError(f"failed to read crontab for '{username}': {result.stderr.strip()}")
    return result.stdout.splitlines()


def _write_raw(username: str, lines: list[str]) -> None:
    content = "\n".join(lines)
    if content and not content.endswith("\n"):
        content += "\n"
    result = run(["crontab", "-u", username, "-"], input_text=content, timeout=10)
    if not result.ok:
        raise CronError(f"failed to write crontab for '{username}': {result.stderr.strip()}")


def get_mailto(username: str) -> str:
    for line in _read_raw(username):
        m = MAILTO_RE.match(line.strip())
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""


def set_mailto(username: str, mailto: str) -> str:
    """A MAILTO=<value> line, when present, must be the crontab's own
    global setting -- not tied to any one job -- so it's kept separate
    from the marker-comment/job-line pairs list_jobs/add_job/etc. work
    with. An empty value removes the line entirely (falls back to cron's
    own native default: mail to the crontab's owner, which is always this
    account's own Linux user -- crontab -u <username>, never root -- so
    this is a reset, not a "suppress all mail" state; see
    validate_cron_mailto's own docstring)."""
    mailto = validate_cron_mailto(mailto)
    lines = [line for line in _read_raw(username) if not MAILTO_RE.match(line.strip())]
    if mailto:
        lines.insert(0, f"MAILTO={mailto}")
    _write_raw(username, lines)
    return mailto


def list_jobs(username: str) -> list[dict]:
    lines = _read_raw(username)
    jobs = []
    i = 0
    while i < len(lines):
        m = MARKER_RE.match(lines[i].strip())
        if m and i + 1 < len(lines):
            job_id, label = m.group(1), m.group(2) or ""
            job_line = lines[i + 1]
            parts = job_line.split(None, 5)
            if len(parts) == 6:
                schedule = " ".join(parts[:5])
                command = parts[5]
                jobs.append({"id": job_id, "label": label, "schedule": schedule, "command": command})
            i += 2
        else:
            i += 1
    return jobs


def add_job(username: str, schedule: str, command: str, label: str = "") -> dict:
    schedule = _validate_schedule(schedule)
    command = _validate_command(command)
    label = _validate_label(label)

    job_id = str(uuid.uuid4())
    lines = _read_raw(username)
    lines.append(f"# boron:id={job_id} label={label}")
    lines.append(f"{schedule} {command}")
    _write_raw(username, lines)
    return {"id": job_id, "label": label, "schedule": schedule, "command": command}


def update_job(username: str, job_id: str, schedule: str, command: str, label: str = "") -> dict:
    schedule = _validate_schedule(schedule)
    command = _validate_command(command)
    label = _validate_label(label)

    lines = _read_raw(username)
    new_lines: list[str] = []
    found = False
    i = 0
    while i < len(lines):
        m = MARKER_RE.match(lines[i].strip())
        if m and m.group(1) == job_id and i + 1 < len(lines):
            new_lines.append(f"# boron:id={job_id} label={label}")
            new_lines.append(f"{schedule} {command}")
            found = True
            i += 2
            continue
        new_lines.append(lines[i])
        i += 1

    if not found:
        raise CronError(f"cron job '{job_id}' not found for '{username}'")
    _write_raw(username, new_lines)
    return {"id": job_id, "label": label, "schedule": schedule, "command": command}


def delete_job(username: str, job_id: str) -> None:
    lines = _read_raw(username)
    new_lines: list[str] = []
    found = False
    i = 0
    while i < len(lines):
        m = MARKER_RE.match(lines[i].strip())
        if m and m.group(1) == job_id and i + 1 < len(lines):
            found = True
            i += 2
            continue
        new_lines.append(lines[i])
        i += 1

    if not found:
        raise CronError(f"cron job '{job_id}' not found for '{username}'")
    _write_raw(username, new_lines)


def delete_all_jobs(username: str) -> None:
    """TERMINATE_HOOKS entry: `userdel --remove` already deletes the user's
    entire crontab as a side effect (matches how Ubuntu's `cron` package
    behaves for any deleted system account), but that only holds if `cron`
    itself is what's watching -- calling this explicitly first makes the
    cleanup independently correct rather than relying on that side effect,
    consistent with every other TERMINATE_HOOKS entry doing its own
    explicit teardown."""
    result = run(["crontab", "-u", username, "-r"], timeout=10)
    if not result.ok and "no crontab for" not in (result.stderr or "").lower():
        raise CronError(f"failed to remove crontab for '{username}': {result.stderr.strip()}")
