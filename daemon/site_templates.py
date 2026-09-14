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
import html
import re
from pathlib import Path

from shared.config import settings
from shared.db import write_session
from shared.models import SuspensionPageSettings, WelcomeEmailTemplate
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
SUSPENSION_DESIGNS = {
    "clean": {"name": "Clean notice", "description": "A calm centered card with a clear support message."},
    "gradient": {"name": "Modern gradient", "description": "A bold branded background with a glass-style notice."},
    "classic": {"name": "Classic hosting", "description": "A familiar traditional hosting suspension page."},
    "minimal": {"name": "Minimal", "description": "A lightweight text-first page for neutral branding."},
}


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
    result = _write_suspended_page(content)
    with write_session() as session:
        row = session.get(SuspensionPageSettings, 1)
        if row is None:
            row = SuspensionPageSettings(id=1)
            session.add(row)
        row.template_key = "custom"
    return result


def _write_suspended_page(content: str) -> dict:
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


def _render_suspension_design(template_key: str, accent_color: str, heading: str, message: str) -> str:
    if template_key not in SUSPENSION_DESIGNS:
        raise ValidationError("unknown suspension template")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", accent_color or ""):
        raise ValidationError("accent_color must be a six-digit hex color")
    if not heading.strip() or len(heading) > 160:
        raise ValidationError("heading is required and must be at most 160 characters")
    if not message.strip() or len(message) > 500:
        raise ValidationError("message is required and must be at most 500 characters")
    color = accent_color.lower()
    title = html.escape(heading.strip())
    body = html.escape(message.strip())
    common = """*{box-sizing:border-box}html,body{margin:0;min-height:100%;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif}body{min-height:100vh;display:grid;place-items:center;padding:24px}main{width:min(680px,100%)}h1{margin:0 0 14px;font-size:clamp(2rem,7vw,4rem);line-height:1.05}p{margin:0;line-height:1.7;font-size:1.05rem}.mark{display:inline-grid;place-items:center;width:58px;height:58px;border-radius:18px;margin-bottom:24px;font-size:28px;font-weight:800}.help{margin-top:28px;font-size:.88rem;opacity:.72}"""
    if template_key == "clean":
        style = f"body{{background:#f1f5f9;color:#0f172a}}main{{background:#fff;border:1px solid #dbe3ee;border-radius:24px;padding:clamp(32px,7vw,64px);box-shadow:0 24px 70px #0f172a14}}.mark{{background:{color}18;color:{color}}}h1{{font-size:clamp(2rem,6vw,3.4rem)}}"
    elif template_key == "gradient":
        style = f"body{{background:linear-gradient(135deg,#090d1a,{color});color:white}}main{{padding:clamp(32px,7vw,64px);border:1px solid #ffffff35;border-radius:28px;background:#ffffff14;box-shadow:0 28px 90px #0006;backdrop-filter:blur(18px)}}.mark{{background:#fff;color:{color}}}"
    elif template_key == "classic":
        style = f"body{{background:#eef1f5;color:#243246}}main{{background:white;border-top:8px solid {color};padding:48px;border-radius:6px;box-shadow:0 12px 35px #1e293b24;text-align:center}}.mark{{background:{color};color:white;border-radius:50%}}h1{{font-family:Georgia,serif;font-size:clamp(2rem,6vw,3rem)}}"
    else:
        style = f"body{{background:white;color:#111827}}main{{border-left:6px solid {color};padding:12px 0 12px clamp(24px,6vw,54px)}}.mark{{display:none}}h1{{font-size:clamp(2rem,6vw,3.2rem)}}"
    return f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{title}</title><style>{common}{style}</style></head><body><main><div class=\"mark\">!</div><h1>{title}</h1><p>{body}</p><p class=\"help\">Reference: account access unavailable</p></main></body></html>"


def get_suspension_designs(params: dict | None = None) -> dict:
    with write_session() as session:
        row = session.get(SuspensionPageSettings, 1)
        current = {
            "template_key": row.template_key if row else "custom",
            "accent_color": row.accent_color if row else "#2563eb",
            "heading": row.heading if row else "Account suspended",
            "message": row.message if row else "Please contact your hosting provider for assistance.",
        }
    previews = [{"key": key, **meta, "html": _render_suspension_design(key, current["accent_color"], current["heading"], current["message"])} for key, meta in SUSPENSION_DESIGNS.items()]
    return {"current": current, "templates": previews}


def apply_suspension_design(params: dict) -> dict:
    template_key = str(params.get("template_key") or "")
    accent_color = str(params.get("accent_color") or "")
    heading = str(params.get("heading") or "")
    message = str(params.get("message") or "")
    content = _render_suspension_design(template_key, accent_color, heading, message)
    result = _write_suspended_page(content)
    with write_session() as session:
        row = session.get(SuspensionPageSettings, 1)
        if row is None:
            row = SuspensionPageSettings(id=1)
            session.add(row)
        row.template_key = template_key
        row.accent_color = accent_color.lower()
        row.heading = heading.strip()
        row.message = message.strip()
    return {**result, "template_key": template_key}


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
