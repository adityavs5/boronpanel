import asyncio

import pytest
from fastapi import Depends, FastAPI, File, UploadFile
from starlette.requests import Request

from api.body_budget import RequestBodyBudget
from api.security import Identity, get_identity
from api.upload_guard import BoundedUploadRoute


def _application(maximum=32):
    app = FastAPI()
    app.add_middleware(RequestBodyBudget, routes=app.router.routes, maximum=maximum)

    @app.post("/json")
    async def body(request: Request):
        return {"size": len(await request.body())}

    @app.post("/protected")
    async def protected(body: dict, identity=Depends(get_identity)):
        raise AssertionError("unauthenticated handler reached")

    return app


def _request(app, path, chunks, headers=()):
    chunks = list(chunks)
    consumed, sent = [], []

    async def receive():
        consumed.append(True)
        if chunks:
            return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}
        # A complete ASGI peer emits disconnect after the last request frame.
        # Returning neither a frame nor a disconnect made Starlette wait
        # forever when response cleanup performed one final receive.
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": "POST", "scheme": "https", "path": path, "raw_path": path.encode(),
                     "query_string": b"", "headers": list(headers), "server": ("panel.example", 443),
                     "client": ("127.0.0.1", 1234)}, receive, send))
    return sent[0]["status"], consumed


@pytest.mark.parametrize("content_type", [b"application/json", b"application/vendor+json", b"multipart/form-data; boundary=x", b"text/plain", None])
def test_ordinary_body_budget_cannot_be_bypassed_by_content_type(content_type):
    headers = [(b"content-type", content_type)] if content_type else []
    status, _ = _request(_application(), "/protected", [b'x' * 33], headers)
    assert status == 413


def test_chunked_body_is_bounded_even_with_false_small_content_length():
    status, consumed = _request(_application(), "/json", [b'a' * 16, b'b' * 17], [(b"content-length", b"1")])
    assert status == 413 and len(consumed) == 2


def test_declared_oversize_rejected_without_reading_body():
    status, consumed = _request(_application(), "/json", [b'ignored'], [(b"content-length", b"33")])
    assert status == 413 and not consumed


def test_exact_budget_and_ordinary_auth_errors_remain_compatible():
    assert _request(_application(), "/json", [b'x' * 32])[0] == 200
    assert _request(_application(), "/protected", [b'{}'], [(b"content-type", b"application/json")])[0] == 401


def test_positive_upload_guard_preserves_large_upload_and_early_auth(monkeypatch):
    from shared.config import settings
    app = FastAPI()
    app.router.route_class = BoundedUploadRoute
    app.add_middleware(RequestBodyBudget, routes=app.router.routes, maximum=32)

    @app.post("/branding/upload")
    async def upload(file: UploadFile = File(...)):
        return {"size": len(await file.read())}

    prefix = b'--test\r\nContent-Disposition: form-data; name="file"; filename="x.bin"\r\n\r\n'
    suffix = b'\r\n--test--\r\n'
    headers = [(b"content-type", b"multipart/form-data; boundary=test")]
    monkeypatch.setattr(settings, "branding_max_upload_bytes", 128)
    status, consumed = _request(app, "/branding/upload", [prefix + b'x' * 64 + suffix], headers)
    assert status == 401 and not consumed
    app.dependency_overrides[get_identity] = lambda: Identity(1, "admin", "admin", None, "session")
    assert _request(app, "/branding/upload", [prefix + b'x' * 64 + suffix], headers)[0] == 200
    assert _request(app, "/branding/upload", [prefix, b'x' * (64 * 1024 + 129), suffix], headers)[0] == 413


def test_streaming_proxy_exemption_is_endpoint_based():
    app = FastAPI()

    @app.post("/stream")
    async def stream(request: Request):
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
        return {"size": size}

    app.add_middleware(RequestBodyBudget, routes=app.router.routes, stream_endpoints=(stream,), maximum=32)
    assert _request(app, "/stream", [b'x' * 64])[0] == 200


def test_guarded_upload_slash_redirect_keeps_large_request():
    app = FastAPI()
    app.router.route_class = BoundedUploadRoute
    app.add_middleware(RequestBodyBudget, routes=app.router.routes, maximum=32)

    @app.post("/branding/upload")
    async def upload(file: UploadFile = File(...)):
        return {}

    status, consumed = _request(app, "/branding/upload/", [b'x' * 64], [(b"content-length", b"64")])
    assert status == 307 and not consumed
