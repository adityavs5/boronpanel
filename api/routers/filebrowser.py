"""File manager v2 — FileBrowser Quantum, fronted by boron-api.

Two surfaces:

* `GET /api/v1/accounts/{username}/files/launch` — the entry point. The SPA
  (customer "Files", admin "File Manager") does a full-page navigation here.
  It authorizes the caller for the account, records an audited "opened files"
  RPC (`fb.open` — this is how admin file access lands in the audit log), sets a
  short-lived **signed** `fh_fb_target` cookie naming the account whose files to
  serve, and 302s to `/files/`.

* `/files` + `/files/{path}` — a reverse proxy to the account-isolated FileBrowser
  Quantum backend. On every request it: resolves the panel identity; reads the
  signed target cookie; **re-authorizes** (`require_account_access`) so an
  expired/ended session or a tampered cookie can't reach another account;
  **strips any client-supplied `X-Fb-User`** and injects the trusted one
  server-side; then streams the request/response through. FileBrowser Quantum is
  never reachable except through this authenticated proxy.

ARCHITECTURE.md §2 note: the panel is deliberately not fronted by OLS, and it
is the process that holds the session — so the trusted-header injection lives
here (boron-api), which is strictly stronger than an OLS-level guess. See
docs/CHECKPOINT-filebrowser-quantum.md.
"""
from __future__ import annotations

import base64
import hashlib
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response, StreamingResponse

from api.rpc import call_daemon
from api.security import (
    COOKIE_NAME,
    Identity,
    get_identity,
    require_account_access,
)
from shared.config import settings
from shared.db import read_session
from shared.models import Account
from shared.filebrowser_paths import account_socket, frontend_socket

api_router = APIRouter(prefix="/api/v1/accounts/{username}/files", tags=["filebrowser"])
proxy_router = APIRouter(tags=["filebrowser-proxy"])

# Signed, short-lived cookie naming which account's files this browser is
# viewing. Separate salt from the session cookie; itsdangerous-signed so a
# client can never forge or edit it (a tampered value fails verification and is
# treated as absent).
FB_TARGET_COOKIE = "fh_fb_target"
FB_TARGET_MAX_AGE_SECONDS = 60 * 60  # a browsing session; re-launch renews it
_target_serializer = URLSafeTimedSerializer(settings.session_secret, salt="boron-fb-target")

PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

# Hop-by-hop headers must not be forwarded across a proxy (RFC 7230 §6.1).
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}

# Reuse an isolated Unix-socket client for each authorized account.
_clients: dict[str, httpx.AsyncClient] = {}


def _frontend_client() -> httpx.AsyncClient:
    return _backend_client('_frontend', frontend_socket())


def _account_client(username: str) -> httpx.AsyncClient:
    return _backend_client(username, account_socket(username))


def _backend_client(username: str, socket_path: str) -> httpx.AsyncClient:
    if username not in _clients:
        _clients[username] = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=socket_path),
            timeout=httpx.Timeout(connect=10.0, read=None, write=None, pool=None),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=2),
        )
    return _clients[username]


def _sign_target(username: str) -> str:
    return _target_serializer.dumps(username)


def _unsign_target(value: str) -> str | None:
    try:
        return _target_serializer.loads(value, max_age=FB_TARGET_MAX_AGE_SECONDS)
    except BadSignature:
        return None


def _resolve_identity(request: Request) -> Identity | None:
    """Like get_identity but returns None instead of raising, so the proxy can
    redirect a browser to login rather than surface a bare 401."""
    try:
        return get_identity(
            request,
            request.headers.get("authorization"),
            request.cookies.get(COOKIE_NAME),
        )
    except HTTPException:
        return None


def _self_username(identity: Identity) -> str | None:
    if identity.account_id is None:
        return None
    with read_session() as db:
        acc = db.get(Account, identity.account_id)
        return acc.username if acc else None


@api_router.get("/launch")
def launch(username: str, identity: Identity = Depends(get_identity)):
    """Authorize + audit + set the target cookie, then redirect into FB Quantum.
    An admin opening a customer's files, or a customer opening their own, both
    go through here; the `fb.open` RPC is audited uniformly (actor/role/target),
    which is how admin file access is logged."""
    require_account_access(identity, username)
    # Validates the account (home exists) and produces the audited access record.
    call_daemon("fb.open", identity, username=username)
    response = RedirectResponse(f"{settings.filebrowser_base_url}/", status_code=302)
    response.set_cookie(
        FB_TARGET_COOKIE,
        _sign_target(username),
        max_age=FB_TARGET_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path=settings.filebrowser_base_url,
    )
    return response


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def _valid_write_origin(request: Request) -> bool:
    """The cookie-authenticated file proxy is a write-capable CSRF surface."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return True
    expected = f"{request.url.scheme}://{request.url.netloc}"
    return request.headers.get("origin") == expected


def _build_upstream_headers(request: Request, target: str) -> list[tuple[str, str]]:
    """Copy the request headers, dropping hop-by-hop, host, content-length
    (httpx sets its own for the streamed body), the client's cookies (FB Quantum
    authenticates only from the injected header — proven stateless in testing —
    so the panel session cookie is never leaked to it), and ANY client-supplied
    copy of the trusted identity header. Then inject the trusted header."""
    header_name = settings.filebrowser_header
    drop = _HOP_BY_HOP | {"host", "content-length", "cookie", header_name.lower()}
    out: list[tuple[str, str]] = [
        (k, v) for k, v in request.headers.items() if k.lower() not in drop
    ]
    out.append((header_name, target))
    return out


def _build_response_headers(upstream: httpx.Response) -> list[tuple[str, str]]:
    return [
        (k, v) for k, v in upstream.headers.items()
        if k.lower() not in _HOP_BY_HOP and k.lower() not in {
            "content-length", "set-cookie", "clear-site-data", "service-worker-allowed", "refresh"
        }
    ]


# Matches inline <script> blocks (no src= attribute). FB Quantum's SPA boots
# from one inline script that sets window.globalVars — its content embeds the
# FB version + branding, so a hardcoded CSP hash would silently break the file
# manager on every FB upgrade (exactly how the "stuck on the loading spinner"
# bug happened: script-src 'self' blocked that inline script and the app never
# rendered). Instead the proxy hashes the inline scripts of the actual HTML it
# serves, per response.
_INLINE_SCRIPT_RE = re.compile(rb"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.S | re.I)


def html_csp(body: bytes) -> str:
    """CSP for the trusted FileBrowser SPA shell only, never hosted files.

    Hashing arbitrary HTML would bless attacker-authored inline scripts from
    an uploaded file. The caller must restrict this policy to the fixed app
    shell URL; all other HTML responses receive an opaque-origin sandbox.
    """
    hashes = " ".join(
        f"'sha256-{base64.b64encode(hashlib.sha256(m).digest()).decode()}'"
        for m in _INLINE_SCRIPT_RE.findall(body)
    )
    script_src = "'self' 'wasm-unsafe-eval'" + (f" {hashes}" if hashes else "")
    return (
        f"default-src 'self'; script-src {script_src}; "
        "worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self' data:; "
        "media-src 'self' blob:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )


def untrusted_html_csp() -> str:
    """Stop customer HTML served by the proxy from running on panel origin."""
    return (
        "sandbox; default-src 'none'; script-src 'none'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    )


def _is_spa_shell(request: Request, upstream: httpx.Response) -> bool:
    return (
        request.method in ("GET", "HEAD")
        and request.url.path.rstrip("/") == settings.filebrowser_base_url.rstrip("/")
        and not request.url.query
        and upstream.status_code == 200
    )


_ACTIVE_DOCUMENT_TYPES = {"image/svg+xml", "application/xhtml+xml", "application/xml", "text/xml"}


def _is_active_document(upstream: httpx.Response) -> bool:
    media_type = upstream.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    return media_type in _ACTIVE_DOCUMENT_TYPES


@proxy_router.api_route(settings.filebrowser_base_url, methods=PROXY_METHODS)
@proxy_router.api_route(settings.filebrowser_base_url + "/{path:path}", methods=PROXY_METHODS)
async def proxy(request: Request, path: str = ""):
    identity = _resolve_identity(request)
    if identity is None:
        if request.method in ("GET", "HEAD") and _wants_html(request):
            return RedirectResponse("/app", status_code=302)
        raise HTTPException(status_code=401, detail="authentication required")
    if identity.auth_method == "session" and not _valid_write_origin(request):
        raise HTTPException(status_code=403, detail="cross-origin file operation rejected")

    # Determine which account's files to serve.
    target: str | None = None
    raw = request.cookies.get(FB_TARGET_COOKIE)
    if raw:
        target = _unsign_target(raw)
    if target is None:
        # No valid target cookie: bootstrap it via the launch endpoint for a
        # customer (their own account); an admin must pick an account first.
        if request.method in ("GET", "HEAD") and _wants_html(request):
            if identity.role == "customer":
                own = _self_username(identity)
                if own:
                    return RedirectResponse(f"/api/v1/accounts/{own}/files/launch", status_code=302)
            return RedirectResponse("/app", status_code=302)
        raise HTTPException(status_code=401, detail="no file-manager session; open Files from the panel")

    # Re-authorize on every request (defense in depth): a customer can only ever
    # reach their own account; an admin can reach any. This also fails closed the
    # instant a session ends/expires.
    require_account_access(identity, target)

    upstream_url = httpx.URL(
        "http://filebrowser" + request.url.path,
        query=request.url.query.encode("utf-8"),
    )
    # Only data APIs reach a tenant-controlled process. The shell and all
    # executable assets come from a separate system UID with no customer homes.
    is_data_api = path == "api" or path.startswith("api/")
    client = _account_client(target) if is_data_api else _frontend_client()
    upstream_req = client.build_request(
        request.method,
        upstream_url,
        headers=_build_upstream_headers(request, target),
        content=request.stream(),
    )
    try:
        upstream = await client.send(upstream_req, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"file manager backend unreachable: {exc}") from exc

    # HTML (the SPA shell, ~15KB) is buffered so its CSP can carry the hashes
    # of its own inline bootstrap script — see html_csp(). Everything else
    # (file downloads, uploads' responses, the SSE event stream) streams
    # through untouched; the main-app CSP middleware leaves /files responses
    # that already carry a CSP alone and stamps its static fallback on the
    # rest.
    if upstream.headers.get("content-type", "").split(";", 1)[0].strip().lower() == "text/html":
        try:
            body = await upstream.aread()
        finally:
            await upstream.aclose()
        headers = dict(_build_response_headers(upstream))
        if _is_spa_shell(request, upstream):
            headers["content-security-policy"] = html_csp(body)
        else:
            headers["content-security-policy"] = untrusted_html_csp()
            headers["content-disposition"] = "attachment"
            headers["x-content-type-options"] = "nosniff"
        return Response(content=body, status_code=upstream.status_code, headers=headers)

    headers = dict(_build_response_headers(upstream))
    if is_data_api or _is_active_document(upstream):
        # An uploaded SVG/XHTML can be a browser document with script execution
        # privileges even though its MIME type is not text/html. Keep bundled
        # FB icons renderable as images while sandboxing direct navigation.
        headers["content-security-policy"] = untrusted_html_csp()
        headers["x-content-type-options"] = "nosniff"
    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=headers,
        background=BackgroundTask(upstream.aclose),
    )
