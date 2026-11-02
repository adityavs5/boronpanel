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

    @staticmethod
    def _dispatched_route(route, scope):
        """Return the leaf route the router will dispatch ``scope`` to.

        FastAPI >= 0.13x registers ``include_router`` as one wrapper route
        whose ``matches`` covers the whole included router. Treating that
        wrapper as the endpoint hid every BoundedUploadRoute behind it, so
        documented 2 GiB uploads were cut at the ordinary 4 MiB budget.
        Descend through wrappers with the router's own selection logic.
        """
        match, _ = route.matches(scope)
        if match != Match.FULL:
            return None
        while hasattr(route, "original_router") and callable(getattr(route, "_match", None)):
            match, child_scope, route, _context = route._match(scope)
            if match != Match.FULL or route is None:
                return None
            scope = {**scope, **child_scope}
        return route

    def _matched_upload_guard(self, scope):
        for candidate in self.routes:
            # Route matching may record router context in the scope; probe a
            # copy so the application receives the request scope unchanged.
            probe = dict(scope)
            match, _ = candidate.matches(probe)
            if match != Match.FULL:
                continue
            route = self._dispatched_route(candidate, probe)
            if route is None:
                return False
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
