"""Run A feature 3: white-label branding.

Panel name, logo, favicon, support email/URL -- a single admin-configured
row (BrandingSettings). Logo/favicon bytes arrive base64-encoded over the
RPC channel (this project's RPC framing is JSON, ARCHITECTURE.md SS2 --
there's no separate binary-upload path to the daemon, so uploads go
through the same op-dispatch mechanism as everything else) and are
written to `settings.branding_dir`, group-readable by boron-api (the
same 0640 root:boron-api pattern shared/db.py's own
`_grant_api_group_read` already uses for the control-plane DB) so the
unprivileged API process can stream them back out over HTTP to anonymous
visitors -- the login page needs to show the branding before any session
exists.
"""
from __future__ import annotations

import base64
import binascii
import html
import logging
import os
import re
from pathlib import Path

from shared.config import settings
from shared.db import write_session
from shared.models import BrandingSettings
from shared.validation import ValidationError, validate_email_address, validate_redirect_target

logger = logging.getLogger("borond.branding")

# Real content-sniffed types only -- a client-supplied filename/extension is
# not trusted (same "don't trust client-declared metadata" posture as every
# upload-accepting feature in this project, e.g. cpanel_import's tarball
# handling). PNG's magic bytes are exact; SVG is text, detected by its root
# element after stripping BOM/whitespace/XML prolog.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_ICO_MAGIC = b"\x00\x00\x01\x00"
_SVG_ROOT_RE = re.compile(rb"<svg[\s>]", re.IGNORECASE)
# SVG is XSS-capable (embedded <script>, on*= event handlers, javascript:
# URIs, <foreignObject> smuggling arbitrary (X)HTML) -- this isn't a full
# XML-aware sanitizer (Audit 3 finding: a real allowlist-based parser is the
# complete fix, deferred as a larger follow-up), just a reject-obvious-cases
# guard. foreignObject is included because it lets an SVG embed a nested
# HTML document (e.g. <foreignObject><iframe srcdoc="...">), which is not a
# real branding-logo use case and has no legitimate reason to appear here.
_SVG_DANGEROUS_RE = re.compile(rb"<script[\s>]|javascript:|on\w+\s*=|<foreignobject[\s>]", re.IGNORECASE)

MAX_PANEL_NAME_LEN = 64


def _entity_decoded(data: bytes) -> bytes:
    """Best-effort XML/HTML entity decoding so an obfuscated payload can't
    smuggle a dangerous construct past the literal substring search above --
    confirmed live that both a numeric-character-reference-obfuscated
    `javascript:` URI (`&#106;avascript:...`) and an entity-encoded
    `<script>` inside a `srcdoc` attribute decode, in a real browser, to the
    exact dangerous strings _SVG_DANGEROUS_RE already rejects, even though
    neither contains the literal substring in the raw bytes. Not a
    replacement for a real XML parse (attribute context still isn't
    understood), just a second pass over the decoded text that closes the
    two confirmed bypasses without a new dependency."""
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return data
    return html.unescape(text).encode("utf-8", errors="replace")


def _sniff_image(data: bytes, allow_ico: bool) -> str:
    if data.startswith(_PNG_MAGIC):
        return "png"
    if allow_ico and data.startswith(_ICO_MAGIC):
        return "ico"
    stripped = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped.startswith(b"<?xml"):
        # Skip past the XML prolog to find the root element.
        end = stripped.find(b"?>")
        stripped = stripped[end + 2 :].lstrip() if end != -1 else stripped
    if _SVG_ROOT_RE.match(stripped) or stripped.startswith(b"<svg"):
        if _SVG_DANGEROUS_RE.search(data) or _SVG_DANGEROUS_RE.search(_entity_decoded(data)):
            raise ValidationError("SVG contains a <script>/event-handler/javascript: URI and was rejected")
        return "svg"
    kinds = "PNG or SVG" if not allow_ico else "PNG, SVG or ICO"
    raise ValidationError(f"unrecognized image data -- must be {kinds}")


def _decode_upload(params: dict, allow_ico: bool = False) -> tuple[bytes, str]:
    raw_b64 = params.get("image_base64")
    if not raw_b64:
        raise ValidationError("image_base64 is required")
    try:
        data = base64.b64decode(raw_b64, validate=True)
    except (binascii.Error, ValueError):
        raise ValidationError("image_base64 is not valid base64") from None
    if not data:
        raise ValidationError("uploaded file is empty")
    if len(data) > settings.branding_max_upload_bytes:
        raise ValidationError(f"file exceeds the {settings.branding_max_upload_bytes} byte limit")
    ext = _sniff_image(data, allow_ico)
    return data, ext


def _branding_dir() -> Path:
    d = Path(settings.branding_dir)
    d.mkdir(parents=True, exist_ok=True, mode=0o755)
    return d


def _grant_api_group_read(path: Path) -> None:
    """Same posture as shared/db.py's `_grant_api_group_read`: only matters
    when actually running as root (borond); tests and other non-root
    callers leave ownership alone."""
    if os.geteuid() != 0:
        return
    import grp

    try:
        gid = grp.getgrnam("boron-api").gr_gid
    except KeyError:
        return
    os.chown(path, 0, gid)
    os.chmod(path, 0o640)
    os.chown(path.parent, 0, gid)
    os.chmod(path.parent, 0o750)


def _get_row(session) -> BrandingSettings:
    row = session.get(BrandingSettings, 1)
    if row is None:
        row = BrandingSettings(id=1)
        session.add(row)
        session.flush()
    return row


def _to_dict(row: BrandingSettings) -> dict:
    return {
        "panel_name": row.panel_name,
        "has_logo": row.logo_filename is not None,
        "has_favicon": row.favicon_filename is not None,
        "support_email": row.support_email,
        "support_url": row.support_url,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_settings(params: dict | None = None) -> dict:
    with write_session() as session:
        return _to_dict(_get_row(session))


def set_settings(params: dict) -> dict:
    with write_session() as session:
        row = _get_row(session)
        if "panel_name" in params:
            name = (params["panel_name"] or "").strip()
            if not name:
                raise ValidationError("panel_name cannot be empty")
            if len(name) > MAX_PANEL_NAME_LEN:
                raise ValidationError(f"panel_name must be at most {MAX_PANEL_NAME_LEN} characters")
            row.panel_name = name
        if "support_email" in params:
            raw = params["support_email"]
            row.support_email = validate_email_address(raw) if raw else None
        if "support_url" in params:
            raw = params["support_url"]
            row.support_url = validate_redirect_target(raw) if raw else None
        session.flush()
        return _to_dict(row)


def _replace_asset(field: str, data: bytes, ext: str) -> str:
    filename = f"{field}.{ext}"
    path = _branding_dir() / filename
    # Remove any stale file with a *different* extension from a previous
    # upload (e.g. logo.png -> logo.svg) so the branding dir never
    # accumulates orphaned assets no row references anymore.
    for existing in _branding_dir().glob(f"{field}.*"):
        if existing.name != filename:
            existing.unlink(missing_ok=True)
    path.write_bytes(data)
    path.chmod(0o644)
    _grant_api_group_read(path)
    return filename


def upload_logo(params: dict) -> dict:
    data, ext = _decode_upload(params, allow_ico=False)
    filename = _replace_asset("logo", data, ext)
    with write_session() as session:
        row = _get_row(session)
        row.logo_filename = filename
        session.flush()
        return _to_dict(row)


def upload_favicon(params: dict) -> dict:
    data, ext = _decode_upload(params, allow_ico=True)
    filename = _replace_asset("favicon", data, ext)
    with write_session() as session:
        row = _get_row(session)
        row.favicon_filename = filename
        session.flush()
        return _to_dict(row)


def _remove_asset(field: str) -> None:
    for existing in _branding_dir().glob(f"{field}.*"):
        existing.unlink(missing_ok=True)


def remove_logo(params: dict | None = None) -> dict:
    _remove_asset("logo")
    with write_session() as session:
        row = _get_row(session)
        row.logo_filename = None
        session.flush()
        return _to_dict(row)


def remove_favicon(params: dict | None = None) -> dict:
    _remove_asset("favicon")
    with write_session() as session:
        row = _get_row(session)
        row.favicon_filename = None
        session.flush()
        return _to_dict(row)
