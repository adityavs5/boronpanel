"""HTTP API served behind nginx at license.boronpanel.com.

Client endpoints (/v1/activate, /v1/check, /v1/updates) are called by panels.
Admin endpoints (/v1/admin/...) need a bearer token created with the CLI; the
admin portal at /admin is a static page that calls them with such a token.
"""

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from boron_license import config, service
from boron_license.db import make_engine, make_sessionmaker
from boron_license.models import AdminToken
from boron_license.service import LicenseError
from boron_license.signing import Signer, load_private_key, public_pem


class ClientRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    license_key: str = Field(max_length=64)
    install_id: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")
    hostname: str = Field(default="", max_length=253)
    version: str = Field(default="", max_length=32, pattern=r"^[0-9A-Za-z.+-]*$")
    account_count: int | None = Field(default=None, ge=0, le=10_000_000)


class IssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: str = Field(max_length=40)
    customer_name: str = Field(max_length=200)
    customer_email: str = Field(max_length=254)
    expires_at: datetime | None = None
    bound_ip: str | None = Field(default=None, max_length=45)
    channel: str = "stable"
    notes: str = Field(default="", max_length=5000)


class UpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: str | None = Field(default=None, max_length=40)
    expires_at: datetime | None = None
    clear_expiry: bool = False
    channel: str | None = None
    customer_name: str | None = Field(default=None, max_length=200)
    customer_email: str | None = Field(default=None, max_length=254)
    notes: str | None = Field(default=None, max_length=5000)


class StatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=500)


class RebindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ip: str | None = Field(default=None, max_length=45)


class UpdatesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    license_key: str = Field(max_length=64)
    install_id: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")
    channel: Literal["stable", "beta"] = "stable"
    # An exact release to fetch (reinstall or pinned upgrade); default: the
    # channel's current release.
    version: str | None = Field(default=None, max_length=20, pattern=r"^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$")
    current_version: str = Field(default="", max_length=32, pattern=r"^[0-9A-Za-z.+-]*$")


class TierRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=40)
    # None means unlimited hosting accounts.
    max_accounts: int | None = Field(default=None, ge=1, le=10_000_000)


ADMIN_DIR = Path(__file__).resolve().parent / "admin"
ADMIN_ASSETS = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
                "app.css": "text/css; charset=utf-8"}
# Same-origin only, no inline code and no third parties. The API is same-origin
# too, so connect-src 'self' covers it.
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; "
                               "frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}


def _utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def create_app(settings: config.Settings | None = None, sessions: sessionmaker[Session] | None = None,
               signer: Signer | None = None) -> FastAPI:
    settings = settings or config.load()
    sessions = sessions or make_sessionmaker(make_engine(settings.database_url))
    signer = signer or Signer(load_private_key(settings.signing_key_path))
    app = FastAPI(title="Boron license server", docs_url=None, redoc_url=None, openapi_url=None)
    # Read once: the portal is part of the deployed code, not user content.
    admin_assets = {name: (ADMIN_DIR / name).read_bytes() for name in ADMIN_ASSETS}

    @app.exception_handler(LicenseError)
    async def license_error(_request: Request, exc: LicenseError) -> JSONResponse:
        return JSONResponse({"error": exc.code, **exc.extra}, status_code=exc.http_status)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    def session_scope() -> Iterator[Session]:
        # Routes commit through transact() before responding; anything left
        # uncommitted here (an unexpected error) is rolled back on close.
        with sessions() as session:
            yield session

    def transact(session: Session, operation):
        try:
            result = operation()
        except LicenseError:
            # Keep the audit record of refused calls (e.g. unknown keys).
            session.commit()
            raise
        session.commit()
        return result

    def client_ip(request: Request) -> str:
        peer = request.client.host if request.client else ""
        forwarded = request.headers.get("x-real-ip", "")
        # nginx sets X-Real-IP to the Cloudflare-restored client address. Any
        # other peer could forge it, so only the local proxy is believed.
        return service.normalize_ip(forwarded if peer in settings.trusted_proxies and forwarded else peer)

    SessionDep = Annotated[Session, Depends(session_scope)]

    def require_admin(session: SessionDep, authorization: Annotated[str, Header()] = "") -> AdminToken:
        scheme, _, token = authorization.partition(" ")
        row = service.authenticate_admin(session, token.strip()) if scheme.lower() == "bearer" else None
        if row is None:
            raise LicenseError("unauthorized", 401)
        return row

    AdminDep = Annotated[AdminToken, Depends(require_admin)]

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "license", "ready": True}

    @app.get("/v1/public-key")
    def public_key() -> dict:
        return {"key_id": signer.key_id, "public_key": public_pem(signer.public_key)}

    def _client_call(body: ClientRequest, request: Request, session: Session, *, bind: bool) -> dict:
        return transact(session, lambda: service.validate(
            session, signer, raw_key=body.license_key, install_id=body.install_id, ip=client_ip(request),
            hostname=body.hostname, version=body.version, account_count=body.account_count, bind=bind,
            grace_days=settings.grace_days))

    @app.post("/v1/activate")
    def activate(body: ClientRequest, request: Request, session: SessionDep) -> dict:
        return _client_call(body, request, session, bind=True)

    @app.post("/v1/check")
    def check(body: ClientRequest, request: Request, session: SessionDep) -> dict:
        return _client_call(body, request, session, bind=False)

    @app.post("/v1/updates")
    def updates(body: UpdatesRequest, request: Request, session: SessionDep) -> dict:
        return transact(session, lambda: service.updates(
            session, raw_key=body.license_key, install_id=body.install_id, ip=client_ip(request),
            channel=body.channel, version=body.version, current_version=body.current_version,
            releases_dir=settings.releases_dir, secret=settings.download_secret,
            base_url=settings.download_base_url, lifetime=settings.link_lifetime))

    # --- Admin portal (static; every action goes through the admin API) -----

    @app.get("/admin", include_in_schema=False)
    def admin_page() -> Response:
        return Response(admin_assets["index.html"], media_type=ADMIN_ASSETS["index.html"])

    @app.get("/admin/", include_in_schema=False)
    def admin_slash() -> RedirectResponse:
        return RedirectResponse("/admin", status_code=308)

    @app.get("/admin/{asset}", include_in_schema=False)
    def admin_asset(asset: str) -> Response:
        if asset not in ADMIN_ASSETS or asset == "index.html":
            return JSONResponse({"error": "not_found"}, status_code=404)
        return Response(admin_assets[asset], media_type=ADMIN_ASSETS[asset])

    # --- Admin --------------------------------------------------------------

    def actor(token: AdminToken) -> str:
        return f"token:{token.name}"

    def described(session: Session, operation) -> dict:
        return transact(session, lambda: {"license": service.describe(operation())})

    @app.get("/v1/admin/me")
    def whoami(admin: AdminDep, session: SessionDep) -> dict:
        return transact(session, lambda: {"token": admin.name})

    @app.get("/v1/admin/tiers")
    def list_tiers(admin: AdminDep, session: SessionDep) -> list[dict]:
        return transact(session, lambda: service.list_tiers(session))

    @app.post("/v1/admin/tiers")
    def set_tier(body: TierRequest, admin: AdminDep, session: SessionDep) -> list[dict]:
        def operation() -> list[dict]:
            service.upsert_tier(session, body.name, body.max_accounts, actor=actor(admin))
            return service.list_tiers(session)
        return transact(session, operation)

    @app.get("/v1/admin/licenses")
    def list_licenses(admin: AdminDep, session: SessionDep, q: str = "", status: str | None = None,
                      limit: int = 100) -> list[dict]:
        return transact(session, lambda: [service.describe(row) for row in
                                          service.search(session, query=q, status=status, limit=limit)])

    @app.post("/v1/admin/licenses", status_code=201)
    def issue_license(body: IssueRequest, admin: AdminDep, session: SessionDep) -> dict:
        def operation() -> dict:
            license_, key = service.issue(session, tier=body.tier, customer_name=body.customer_name,
                                          customer_email=body.customer_email, actor=actor(admin),
                                          expires_at=_utc(body.expires_at), bound_ip=body.bound_ip,
                                          channel=body.channel, notes=body.notes)
            # The only time the key is ever returned.
            return {"license": service.describe(license_), "license_key": key}
        return transact(session, operation)

    @app.get("/v1/admin/licenses/{license_id}")
    def show_license(license_id: int, admin: AdminDep, session: SessionDep) -> dict:
        return transact(session, lambda: {
            "license": service.describe(service.get_license(session, license_id)),
            "installations": service.installations(session, license_id)})

    @app.get("/v1/admin/licenses/{license_id}/events")
    def license_events(license_id: int, admin: AdminDep, session: SessionDep, limit: int = 50) -> list[dict]:
        return transact(session, lambda: service.events(session, license_id, limit=limit))

    @app.patch("/v1/admin/licenses/{license_id}")
    def update_license(license_id: int, body: UpdateRequest, admin: AdminDep, session: SessionDep) -> dict:
        return described(session, lambda: service.update(
            session, license_id, actor=actor(admin), tier=body.tier, expires_at=_utc(body.expires_at),
            clear_expiry=body.clear_expiry, channel=body.channel, customer_name=body.customer_name,
            customer_email=body.customer_email, notes=body.notes))

    for action, status in (("suspend", "suspended"), ("resume", "active"), ("cancel", "cancelled")):
        def _status_route(license_id: int, body: StatusRequest, admin: AdminDep, session: SessionDep,
                          _status: str = status) -> dict:
            return described(session, lambda: service.set_status(
                session, license_id, _status, actor=actor(admin), reason=body.reason))
        app.post(f"/v1/admin/licenses/{{license_id}}/{action}", name=f"{action}_license")(_status_route)

    @app.post("/v1/admin/licenses/{license_id}/rebind")
    def rebind_license(license_id: int, body: RebindRequest, admin: AdminDep, session: SessionDep) -> dict:
        return described(session, lambda: service.rebind(session, license_id, body.ip, actor=actor(admin)))

    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn --factory boron_license.api:app_factory``."""
    return create_app()
