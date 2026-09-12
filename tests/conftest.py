"""Keep every test process away from the development SQLite file."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import target_database


@pytest.fixture(autouse=True)
def isolate_default_database(tmp_path, monkeypatch):
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
    monkeypatch.setattr(target_database, "engine", engine)
    monkeypatch.setattr(target_database, "SessionLocal", sessions)
    try:
        yield
    finally:
        engine.dispose()
