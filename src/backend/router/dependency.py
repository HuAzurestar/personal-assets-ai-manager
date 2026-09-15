"""Dependencies shared by PAAM HTTP routers."""

from collections.abc import Generator

from sqlalchemy.orm import Session

from backend.core import target_database


def get_db() -> Generator[Session, None, None]:
    db = target_database.SessionLocal()
    try:
        yield db
    finally:
        db.close()
