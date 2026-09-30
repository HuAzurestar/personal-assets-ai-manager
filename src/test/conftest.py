"""Keep every test process away from the development SQLite file."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core import target_database


def pytest_addoption(parser):
    parser.addoption(
        "--run-browser", action="store_true", default=False,
        help="Run fictional-data browser regressions in separate processes.",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-browser"):
        return
    skip = pytest.mark.skip(reason="Use --run-browser with Playwright and a browser installed")
    for item in items:
        if "browser" in item.keywords:
            item.add_marker(skip)


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
