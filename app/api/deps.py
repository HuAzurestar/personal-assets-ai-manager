from collections.abc import Generator

from sqlalchemy.orm import Session

from app import database


def get_db() -> Generator[Session, None, None]:
    # Resolve the factory at call time so tests and alternate executors can bind
    # one session factory without patching every controller module.
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()
