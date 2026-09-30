"""Domain error translation shared by PAAM HTTP routers."""

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from backend.error import DomainError
from backend.schema.response import ErrorBody, ErrorResponse


logger = logging.getLogger(__name__)

_SENSITIVE_FIELDS = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "token",
}


def _is_sensitive_field(value: object) -> bool:
    if not isinstance(value, str):
        return False
    key = value.casefold()
    return (
        key in _SENSITIVE_FIELDS
        or key.endswith("_api_key")
        or "password" in key
        or "secret" in key
    )


def _redact_sensitive_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _is_sensitive_field(key) else _redact_sensitive_values(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_values(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_sensitive_values(item) for item in value)
    return value


def _redact_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw_error in errors:
        error = _redact_sensitive_values(dict(raw_error))
        # An unknown field can contain a credential even when its name gives no
        # clue (for example, an extra "access_token" field). The input value is
        # not needed to locate or explain a validation failure.
        error.pop("input", None)
        result.append(error)
    return result


def _error_response(
    status_code: int,
    message: str,
    code: str,
    *,
    details: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    response = ErrorResponse(
        status=status_code,
        message=message,
        body=ErrorBody(
            code=code,
            details=jsonable_encoder(details) if details is not None else None,
        ),
    )
    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content=response.model_dump(mode="json"),
    )


def _domain_error_response(error: DomainError) -> JSONResponse:
    return _error_response(
        error.status_code,
        str(error),
        error.code,
        details=error.details or None,
    )


def _validation_error_response(error: RequestValidationError) -> JSONResponse:
    return _error_response(
        422,
        "Request validation failed",
        "VALIDATION_ERROR",
        details=_redact_validation_errors(error.errors()),
    )


def _http_error_response(error: HTTPException) -> JSONResponse:
    message = error.detail if isinstance(error.detail, str) else "Request failed"
    details = None if isinstance(error.detail, str) else error.detail
    return _error_response(
        error.status_code,
        message,
        f"HTTP_{error.status_code}",
        details=details,
        headers=error.headers,
    )


def _internal_error_response(request: Request, error: Exception) -> JSONResponse:
    logger.error(
        "Unhandled error while processing %s",
        request.url.path,
        exc_info=error,
    )
    return _error_response(
        500,
        "Internal server error",
        "INTERNAL_SERVER_ERROR",
    )


async def _domain_error_handler(_: Request, error: DomainError) -> JSONResponse:
    return _domain_error_response(error)


async def _validation_error_handler(
    _: Request,
    error: RequestValidationError,
) -> JSONResponse:
    return _validation_error_response(error)


async def _http_error_handler(_: Request, error: HTTPException) -> JSONResponse:
    return _http_error_response(error)


async def _internal_error_handler(request: Request, error: Exception) -> JSONResponse:
    return _internal_error_response(request, error)


def register_error_handlers(app: FastAPI) -> None:
    """Apply the error contract to failures outside a matched business route."""

    app.add_exception_handler(DomainError, _domain_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(HTTPException, _http_error_handler)
    app.add_exception_handler(Exception, _internal_error_handler)


class DomainErrorRoute(APIRoute):
    """Keep the error-response contract attached to every business Router."""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def translated(request: Request) -> Response:
            try:
                return await original(request)
            except DomainError as error:
                return _domain_error_response(error)
            except RequestValidationError as error:
                return _validation_error_response(error)
            except HTTPException as error:
                return _http_error_response(error)
            except Exception as error:
                return _internal_error_response(request, error)

        return translated
