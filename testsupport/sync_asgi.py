"""Same-thread ASGI client for deterministic unit tests.

Starlette's ``TestClient`` drives the application through an AnyIO portal
thread. Some constrained CI/sandbox runtimes suppress that portal's
cross-thread event-loop wakeup, leaving both otherwise-idle threads waiting
forever. This adapter drives HTTPX's ASGI transport on the calling thread.
It implements the ordinary HTTP surface used by Boron's tests; WebSocket and
lifespan behavior have dedicated async tests.
"""
from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Any
from unittest.mock import patch

import anyio.to_thread
import httpx


class SyncASGIClient:
    """Synchronous facade over ``httpx.AsyncClient`` and ``ASGITransport``."""

    __test__ = False

    def __init__(
        self,
        app: Any,
        *,
        base_url: str = "http://testserver",
        follow_redirects: bool = True,
        raise_server_exceptions: bool = True,
        headers: dict[str, str] | None = None,
        **_unused: Any,
    ) -> None:
        self.app = app
        self.base_url = base_url
        self.follow_redirects = follow_redirects
        self.raise_server_exceptions = raise_server_exceptions
        self.headers = httpx.Headers(headers or {})
        self.headers.setdefault("user-agent", "testclient")
        self.cookies = httpx.Cookies()

    def __enter__(self) -> "SyncASGIClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Match TestClient; each request closes its own transport."""

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        async def run_sync_inline(func: Any, *args: Any, **_options: Any) -> Any:
            # Starlette dispatches synchronous dependencies and route handlers
            # through AnyIO's worker pool. Keep those calls on this same test
            # loop as well, otherwise the constrained-runner wakeup defect
            # merely moves from TestClient's portal to the worker completion.
            return func(*args)

        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(
                app=self.app,
                raise_app_exceptions=self.raise_server_exceptions,
                client=("testclient", 50000),
            )
            with patch.object(anyio.to_thread, "run_sync", run_sync_inline):
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url=self.base_url,
                    headers=self.headers,
                    cookies=self.cookies,
                    follow_redirects=self.follow_redirects,
                ) as client:
                    response = await client.request(method, url, **kwargs)
                    await response.aread()
                    self.cookies.update(client.cookies)
                    return response

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(send())
        raise RuntimeError(
            "SyncASGIClient cannot run inside an active event loop; use "
            "httpx.AsyncClient with ASGITransport in async tests"
        )

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)

    def options(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("OPTIONS", url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("HEAD", url, **kwargs)
