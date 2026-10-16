"""Application error hierarchy and the consistent JSON error envelope.

Every error leaving the API has the shape::

    {"error": {"code": "SOME_CODE", "message": "Human readable", "details": {}}}

Unexpected exceptions are logged with their traceback and returned as INTERNAL_ERROR
without any internal detail.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

log = get_logger(__name__)


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"
    message = "Bad request"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.details = details or {}
        self.headers = headers
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.message)


class ValidationFailed(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"
    message = "Invalid request"


class Unauthorized(AppError):
    status_code = 401
    code = "UNAUTHORIZED"
    message = "Authentication required"


class Forbidden(AppError):
    status_code = 403
    code = "FORBIDDEN"
    message = "You are not allowed to perform this action"


class NotFound(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "Resource not found"


class Conflict(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "Conflict"


class PayloadTooLarge(AppError):
    status_code = 413
    code = "PAYLOAD_TOO_LARGE"
    message = "Payload too large"


class RateLimited(AppError):
    status_code = 429
    code = "RATE_LIMITED"
    message = "Too many requests, slow down"


class QuotaExceeded(AppError):
    status_code = 429
    code = "QUOTA_EXCEEDED"
    message = "Resource quota exceeded"


class ServiceUnavailable(AppError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    message = "A required service is unavailable, try again later"


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "RATE_LIMITED",
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            log.warning("app_error", code=exc.code, message=exc.message)
        return JSONResponse(
            error_body(exc.code, exc.message, exc.details),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ()) if p != "body"),
                "message": err.get("msg", "invalid"),
                "type": err.get("type", "value_error"),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            error_body("VALIDATION_ERROR", "Invalid request", {"errors": errors}),
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ").capitalize()
        return JSONResponse(
            error_body(code, message),
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", error_type=type(exc).__name__)
        return JSONResponse(
            error_body("INTERNAL_ERROR", "An unexpected error occurred"),
            status_code=500,
        )
