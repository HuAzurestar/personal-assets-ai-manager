"""Dependencies shared by PAAM HTTP routers."""

from collections.abc import Collection, Generator

from fastapi import Request
from sqlalchemy.orm import Session

from backend.core import (
    ProtectedSecretStore,
    protected_secret_store,
    target_database,
)
from backend.error import ListQueryError


def get_db() -> Generator[Session, None, None]:
    db = target_database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_protected_secret_store() -> ProtectedSecretStore:
    return protected_secret_store


def get_middleware(request: Request):
    middleware = getattr(request.app.state, "middleware", None)
    if middleware is None:
        from backend.middleware.composition import create_middleware
        from backend.core.job_scheduler import job_scheduler
        middleware = create_middleware(target_database.SessionLocal, protected_secret_store, job_scheduler)
    return middleware


def get_platform_middleware(request: Request):
    return get_middleware(request).platform


def validate_query_parameter_names(
    request: Request,
    allowed: Collection[str],
) -> None:
    unsupported = sorted(set(request.query_params) - set(allowed))
    if unsupported:
        raise ListQueryError(
            "Query parameter is not supported",
            code="LIST_PARAMETER_NOT_SUPPORTED",
            details={
                "component": "request",
                "parameters": unsupported,
                "supported": sorted(allowed),
            },
        )
