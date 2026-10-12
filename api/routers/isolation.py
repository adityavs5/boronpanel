import httpx
from fastapi import APIRouter, Depends
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import COOKIE_NAME, Identity, get_identity, require_admin
from api.routers import filebrowser
from shared.config import settings

api_router = APIRouter(prefix="/api/v1/admin/isolation", tags=["isolation"])


@api_router.get("")
def overview(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("namespace.overview", identity)


async def _file_manager_proxy_check(request: Request, username: str, identity: Identity) -> dict:
    """Exercise the authenticated `/files/` proxy used by the browser."""
    try:
        call_daemon("fb.open", identity, username=username)
        headers = {"accept": "text/html"}
        if request.headers.get("authorization"):
            headers["authorization"] = request.headers["authorization"]
        cookies = {filebrowser.FB_TARGET_COOKIE: filebrowser._sign_target(username)}
        if request.cookies.get(COOKIE_NAME):
            cookies[COOKIE_NAME] = request.cookies[COOKIE_NAME]
        transport = httpx.ASGITransport(app=request.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://boron.internal",
            follow_redirects=False,
            timeout=15,
        ) as client:
            response = await client.get(
                settings.filebrowser_base_url.rstrip("/") + "/",
                headers=headers,
                cookies=cookies,
            )
        if response.status_code == 200:
            return {"status": "passed", "explanation": "The authenticated File Manager proxy opened for this account."}
        return {
            "status": "failed",
            "explanation": f"The File Manager proxy returned HTTP {response.status_code}.",
        }
    except Exception:
        return {
            "status": "failed",
            "explanation": "The authenticated File Manager proxy could not open for this account.",
        }


@api_router.post("/{username}/rebuild")
async def rebuild(request: Request, username: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    rebuilt = call_daemon("namespace.rebuild", identity, username=username)
    proxy_check = await _file_manager_proxy_check(request, username, identity)
    tested = call_daemon(
        "namespace.self_test", identity, username=username, file_manager_check=proxy_check,
    )
    return {**rebuilt, "self_test": tested, "checks": tested["checks"], "tested_at": tested["tested_at"]}


@api_router.post("/{username}/self-test")
async def self_test(request: Request, username: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    proxy_check = await _file_manager_proxy_check(request, username, identity)
    return call_daemon(
        "namespace.self_test", identity, username=username, file_manager_check=proxy_check,
    )
