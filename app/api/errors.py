"""The one error shape every /api/v1 response uses (see docs/api-v1.md#errors)."""
import logging
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Optional[dict] = None):
        self.status, self.code, self.message, self.details = status, code, message, details


def not_implemented() -> ApiError:
    return ApiError(501, "not_implemented", "This endpoint is in the v1 contract but not built yet.")


def _body(request: Request, code: str, message: str, details: Optional[dict] = None) -> dict:
    error: dict[str, Any] = {"code": code, "message": message, "requestId": request.headers.get("x-request-id")}
    if details:
        error["details"] = details
    return {"error": error}


logger = logging.getLogger(__name__)

_STATUS_CODES = {400: "bad_request", 401: "unauthenticated", 403: "forbidden", 404: "not_found",
                 405: "method_not_allowed", 409: "conflict", 429: "rate_limited"}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return JSONResponse(_body(request, exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        fields = [
            {"field": ".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]), "message": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            _body(request, "validation_error", "Some fields are invalid.", {"fields": fields}), status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = _STATUS_CODES.get(exc.status_code, "error")
        return JSONResponse(_body(request, code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(_body(request, "internal_error", "Something went wrong on our side."), status_code=500)
