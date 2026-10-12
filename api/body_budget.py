"""Bound ordinary request bodies before framework parsing allocates them.

Large uploads remain on their existing authenticated bounded/streaming
routes. A client-provided Content-Type never grants an upload exemption.
"""
from fastapi import params
from starlette.exceptions import HTTPException
from starlette.routing import Match

from api.upload_guard import BoundedUploadRoute
from api.error_context import error_response

MAX_REQUEST_BODY_BYTES = 4 * 1024 * 1024


class RequestBodyBudget:
    def __init__(self, app, *, routes, stream_endpoints=(), maximum=MAX_REQUEST_BODY_BYTES):
        self.app = app
        self.routes = routes
        self.stream_endpoints = frozenset(stream_endpoints)
        self.maximum = maximum

    def _matched_upload_guard(self, scope):
        for route in self.routes:
            match, _ = route.matches(scope)
            if match != Match.FULL:
                continue
            if getattr(route, "endpoint", None) in self.stream_endpoints:
                return True
            return (isinstance(route, BoundedUploadRoute) and route.body_field is not None
                    and isinstance(route.body_field.field_info, params.File))
        return None

    def _has_upload_guard(self, scope):
        guarded = self._matched_upload_guard(scope)
        if guarded is not None:
            return guarded
        # Starlette redirects a trailing-slash variant before reading its
        # body. Preserve large uploads through that canonical redirect too.
        path = scope.get("path", "")
        if path and path != "/":
            alternate = path.rstrip("/") if path.endswith("/") else path + "/"
            return self._matched_upload_guard({**scope, "path": alternate}) is True
        return False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self._has_upload_guard(scope):
            return await self.app(scope, receive, send)
        for key, value in scope.get("headers", ()):
            if key.lower() != b"content-length":
                continue
            try:
                length = int(value)
                if length < 0:
                    raise ValueError
            except ValueError:
                response = error_response("invalid content length", 400)
                return await response(scope, receive, send)
            if length > self.maximum:
                response = error_response("request exceeds the size limit", 413)
                return await response(scope, receive, send)
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.maximum:
                    # FastAPI preserves HTTPException during body parsing.
                    raise HTTPException(413, "request exceeds the size limit")
            return message

        return await self.app(scope, bounded_receive, send)
