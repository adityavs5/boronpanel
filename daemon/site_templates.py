"""QA round 2, item 10: admin-editable suspension page + welcome email
template.

Both were previously fixed content with no editing surface at all:
- The suspension page (ARCHITECTURE.md SS10) is a single, server-wide,
  root-owned static file at settings.suspended_page_root/index.html,
  written ONCE, at install time, by scripts/install.sh's own heredoc --
  no daemon module or RPC op ever read/wrote it again after that.
- The "account created" welcome email's subject/body were hardcoded
  Python strings in daemon/notifications.py's _subjects()/_render_body(),
  with no way to change them short of editing and redeploying code.

Deliberately admin-only (both are server-wide, not scoped to any one
account) and deliberately NOT folded into daemon/custom_pages.py's
per-domain error-page CRUD (that system is keyed by (username, domain);
the suspension page has neither -- it's the one page served identically
for every suspended account on the box).
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import WelcomeEmailTemplate
from shared.validation import ValidationError

MAX_PAGE_BYTES = 512 * 1024  # generous for a real branded HTML page, bounded against abuse
MAX_SUBJECT_LEN = 200
MAX_BODY_LEN = 8000

# The exact set of tokens daemon/notifications.py's welcome-email renderer
# substitutes -- documented here so the admin UI can show them, and so
# set_welcome_email_template can reject an unknown {{token}} before it's
# ever saved (a typo'd placeholder would otherwise render literally into
# every future welcome email, silently, until someone happened to notice).
WELCOME_EMAIL_PLACEHOLDERS = ("username", "password", "primary_domain", "panel_name")


class SiteTemplatesError(Exception):
    pass


def _validate_html(content: str) -> str:
    if not isinstance(content, str) or not content.strip():
        raise ValidationError("page content must not be empty")
    if len(content.encode("utf-8")) > MAX_PAGE_BYTES:
        raise ValidationError(f"page content must be at most {MAX_PAGE_BYTES} bytes")
    if "\x00" in content:
        raise ValidationError("page content must not contain a NUL byte")
    return content


def _suspended_page_path() -> Path:
    return Path(settings.suspended_page_root) / "index.html"


def get_suspended_page(params: dict) -> dict:
    path = _suspended_page_path()
    if not path.exists():
        raise SiteTemplatesError(f"'{path}' does not exist -- run the installer's setup steps first")
    return {"content": path.read_text(errors="replace")}


def set_suspended_page(params: dict) -> dict:
    """Backup-then-atomic-replace, the same shape as ARCHITECTURE.md SS7's
    validate->backup->apply pattern minus the "reload a service" step --
    this is a static file OLS re-reads fresh on every request, so there is
    no service to reload/verify, unlike a vhost config. The backup still
    matters: a bad admin-authored page (or an accidental empty save had
    validation not caught it) is trivially recoverable."""
    content = _validate_html(params.get("content", ""))
    path = _suspended_page_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        backup_dir = Path(settings.backup_dir) / "suspended-page" / str(int(time.time()))
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_dir / "index.html")

    tmp_path = path.with_suffix(".html.tmp")
    tmp_path.write_text(content)
    tmp_path.chmod(0o644)
    tmp_path.replace(path)  # atomic on the same filesystem
    return {"content": content, "status": "saved"}


def _row_to_dict(row: WelcomeEmailTemplate | None) -> dict:
    return {
        "subject": row.subject if row else None,
        "body": row.body if row else None,
        "placeholders": list(WELCOME_EMAIL_PLACEHOLDERS),
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
    }


def _validate_placeholders(body: str) -> None:
    import re

    for token in re.findall(r"\{\{\s*([a-zA-Z_]+)\s*\}\}", body):
        if token not in WELCOME_EMAIL_PLACEHOLDERS:
            raise ValidationError(
                f"unknown placeholder '{{{{{token}}}}}' -- supported: {', '.join(WELCOME_EMAIL_PLACEHOLDERS)}"
            )


def get_welcome_email_template(params: dict) -> dict:
    with write_session() as session:
        row = session.get(WelcomeEmailTemplate, 1)
        return _row_to_dict(row)


def set_welcome_email_template(params: dict) -> dict:
    subject = (params.get("subject") or "").strip() or None
    body = (params.get("body") or "").strip() or None
    if subject is not None and len(subject) > MAX_SUBJECT_LEN:
        raise ValidationError(f"subject must be at most {MAX_SUBJECT_LEN} characters")
    if body is not None:
        if len(body) > MAX_BODY_LEN:
            raise ValidationError(f"body must be at most {MAX_BODY_LEN} characters")
        _validate_placeholders(body)

    with write_session() as session:
        row = session.get(WelcomeEmailTemplate, 1)
        if row is None:
            row = WelcomeEmailTemplate(id=1)
            session.add(row)
        row.subject = subject
        row.body = body
        session.flush()
        return _row_to_dict(row)


def reset_welcome_email_template(params: dict) -> dict:
    """Revert to the built-in default (daemon/notifications.py's own
    hardcoded text) -- deletes the row entirely rather than setting both
    fields to None via set_welcome_email_template, so get_overrides-style
    callers can tell "never customized" apart from "customized to nothing"
    identically (there's no meaningful difference for this template, but
    matches the row-presence convention every other override table in this
    schema uses)."""
    with write_session() as session:
        row = session.get(WelcomeEmailTemplate, 1)
        if row is not None:
            session.delete(row)
    return {"status": "reset"}
