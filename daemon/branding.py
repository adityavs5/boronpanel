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
import logging
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from shared.config import settings
from shared.db import write_session
from shared.models import BrandingSettings
from shared.terminal_welcome import DEFAULT_TERMINAL_BANNER, validate_terminal_banner
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
_SVG_PROLOG_RE = re.compile(rb'^<\?xml\s+version=["\']1\.0["\']\s*\?>', re.IGNORECASE)
_SVG_NS = "http://www.w3.org/2000/svg"
_SVG_TAGS = frozenset({"svg", "g", "path", "rect", "circle", "ellipse", "line",
                       "polyline", "polygon", "title", "desc", "defs",
                       "linearGradient", "radialGradient", "stop", "clipPath"})
_SVG_ATTRS = frozenset({
    "id", "width", "height", "viewBox", "preserveAspectRatio", "x", "y",
    "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry", "d", "points",
    "fill", "fill-rule", "fill-opacity", "stroke", "stroke-width",
    "stroke-linecap", "stroke-linejoin", "stroke-opacity", "stroke-dasharray",
    "opacity", "transform", "offset", "stop-color", "stop-opacity",
    "gradientUnits", "gradientTransform", "clip-path",
})
_SVG_VALUE_RE = re.compile(r"^[A-Za-z0-9#.,()%+\-/\s]*$")
_SVG_LOCAL_REF_RE = re.compile(r"url\(#[A-Za-z][A-Za-z0-9_-]{0,63}\)")
ET.register_namespace("", _SVG_NS)

MAX_PANEL_NAME_LEN = 64


def _sanitize_svg(data: bytes) -> bytes:
    """Accept a small drawing-only SVG vocabulary and serialize a fresh tree.

    No DTD, processing instruction, foreign namespace, URL attribute, style,
    animation or embedded HTML survives. Validation happens after XML entity
    decoding by the parser, and only the serialized output is stored.
    """
    stripped = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    stripped = _SVG_PROLOG_RE.sub(b"", stripped, count=1).lstrip()
    if b"<!" in stripped or b"<?" in stripped:
        raise ValidationError("SVG declarations and processing instructions are not allowed")
    try:
        root = ET.fromstring(stripped)
    except ET.ParseError as exc:
        raise ValidationError("invalid SVG XML") from exc
    if root.tag != f"{{{_SVG_NS}}}svg":
        raise ValidationError("SVG root must use the SVG namespace")
    nodes = 0
    pending = [(root, 0)]
    while pending:
        element, depth = pending.pop()
        nodes += 1
        if nodes > 4096 or depth > 32:
            raise ValidationError("SVG is too complex")
        if element.tag not in {f"{{{_SVG_NS}}}{name}" for name in _SVG_TAGS}:
            raise ValidationError("unsupported SVG element")
        if element.tail and element.tail.strip():
            raise ValidationError("SVG mixed content is not allowed")
        if element.tag not in (f"{{{_SVG_NS}}}title", f"{{{_SVG_NS}}}desc") and element.text and element.text.strip():
            raise ValidationError("SVG text is only allowed in title/desc")
        for key, value in element.attrib.items():
            if key not in _SVG_ATTRS or len(value) > 16384 or not _SVG_VALUE_RE.fullmatch(value):
                raise ValidationError("unsupported SVG attribute or value")
            if "url(" in value.lower():
                if key not in ("fill", "stroke", "clip-path") or not _SVG_LOCAL_REF_RE.fullmatch(value):
                    raise ValidationError("external SVG references are not allowed")
            if key == "id" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value):
                raise ValidationError("invalid SVG identifier")
        pending.extend((child, depth + 1) for child in element)
    return ET.tostring(root, encoding="utf-8", xml_declaration=False)


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
        _sanitize_svg(data)
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
    if ext == "svg":
        data = _sanitize_svg(data)
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
        "terminal_banner": row.terminal_banner,
        "default_terminal_banner": DEFAULT_TERMINAL_BANNER,
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
        if "terminal_banner" in params:
            row.terminal_banner=validate_terminal_banner(params["terminal_banner"])
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
