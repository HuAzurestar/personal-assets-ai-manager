"""Dependencies shared by PAAM HTTP routers."""

from collections.abc import Collection, Generator

from fastapi import Request
from sqlalchemy.orm import Session

from backend.core import target_database
from backend.error import ListQueryError


def get_db() -> Generator[Session, None, None]:
    db = target_database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
