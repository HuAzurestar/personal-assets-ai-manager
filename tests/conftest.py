"""Keep every test process away from the development SQLite file."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import database


@pytest.fixture(autouse=True)
def isolate_default_database(tmp_path, monkeypatch):
    """Provide a disposable default even when a legacy test forgets to bind one."""

    path = tmp_path / "default-runtime.db"
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )
    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    try:
        yield
    finally:
        engine.dispose()
