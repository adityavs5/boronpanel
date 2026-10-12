"""Safe, additive error responses for the public HTTP boundary."""
from __future__ import annotations

from typing import Any, Mapping

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from shared.request_context import ensure_error_reference


def error_response(
    detail: Any,
    status_code: int,
    *,
    headers: Mapping[str, str] | None = None,
    reference: str | None = None,
) -> JSONResponse:
    ref = ensure_error_reference(reference)
    response_headers = dict(headers or {})
    response_headers["X-Boron-Error-Reference"] = ref
    return JSONResponse(
        jsonable_encoder({"detail": detail, "reference": ref}),
        status_code=status_code,
        headers=response_headers,
    )
