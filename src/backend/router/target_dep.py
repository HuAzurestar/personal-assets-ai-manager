"""Dependencies owned by the target PIRC-9 runtime."""

from collections.abc import Generator

from sqlalchemy.orm import Session

from backend.core import target_database


def get_target_db() -> Generator[Session, None, None]:
    db = target_database.SessionLocal()
    try:
        yield db
    finally:
        db.close()
