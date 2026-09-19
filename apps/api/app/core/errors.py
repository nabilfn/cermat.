"""Standardised API errors.

Every error response has the same shape:

    {"error": {"code": "NOT_FOUND", "message": "...", "request_id": "..."}}

Messages are written for users. Stack traces, SQL and provider details are
logged server-side with the request id and never returned to clients.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import request_id_var

logger = logging.getLogger("cermat.errors")

ERROR_CODES = {
    "VALIDATION_ERROR",
    "UNAUTHORIZED",
    "FORBIDDEN",
    "NOT_FOUND",
    "CONFLICT",
    "UPLOAD_TOO_LARGE",
    "UNSUPPORTED_DOCUMENT",
    "EXTRACTION_FAILED",
    "RECONCILIATION_FAILED",
    "AI_PROVIDER_ERROR",
    "RATE_LIMITED",
    "INTERNAL_ERROR",
}

STATUS_CODES = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "VALIDATION_ERROR",
    409: "CONFLICT",
    413: "UPLOAD_TOO_LARGE",
    415: "UNSUPPORTED_DOCUMENT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    502: "AI_PROVIDER_ERROR",
    503: "AI_PROVIDER_ERROR",
}


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        assert code in ERROR_CODES, code
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


def not_found(what: str = "Resource") -> ApiError:
    # Used for missing *and* foreign-workspace resources, so ids cannot be probed.
    return ApiError(404, "NOT_FOUND", f"{what} not found.")


def error_body(code: str, message: str, details: list[dict[str, Any]] | None = None) -> dict:
    body: dict[str, Any] = {"code": code, "message": message, "request_id": request_id_var.get()}
    if details:
        body["details"] = details
    return {"error": body}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            error_body(exc.code, exc.message, exc.details),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = STATUS_CODES.get(exc.status_code, "INTERNAL_ERROR" if exc.status_code >= 500 else "VALIDATION_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        if exc.status_code == 404 and message == "Not Found":
            message = "Not found."
        return JSONResponse(error_body(code, message), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Field locations and messages only — never echo submitted values back.
        details = [
            {"field": ".".join(str(part) for part in error.get("loc", ()) if part != "body"), "message": error.get("msg", "Invalid value.")}
            for error in exc.errors()[:20]
        ]
        return JSONResponse(
            error_body("VALIDATION_ERROR", "Some fields are missing or invalid.", details),
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", extra={"event": "unhandled_error"})
        return JSONResponse(
            error_body("INTERNAL_ERROR", "Something went wrong on our side. Please try again."),
            status_code=500,
        )
