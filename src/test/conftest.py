"""Keep every test process away from the development SQLite file."""

import pytest
from collections import deque
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core import target_database
from backend.core.import_preview_store import import_preview_store
from backend.core.feature_observability import observability


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
    import_preview_store.clear()
    # Lifespan configures app-owned logs, but tests must never append to the
    # developer's operational directory or share counters across test cases.
    configure = observability.configure
    monkeypatch.setattr(observability, "directory", tmp_path / "operational-logs")
    monkeypatch.setattr(observability, "configure", lambda _: configure(tmp_path / "operational-logs"))
    monkeypatch.setattr(observability, "_metrics", {})
    monkeypatch.setattr(observability, "_events", deque(maxlen=100))
    monkeypatch.setattr(observability, "storage_unavailable", False)
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
        import_preview_store.clear()
        engine.dispose()
