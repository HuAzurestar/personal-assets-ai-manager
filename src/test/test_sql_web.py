from __future__ import annotations

from a2wsgi import WSGIMiddleware
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from backend.core import sql_web
from backend.core.target_database import init_target_db


def test_sql_browser_mount_reads_current_schema_but_cannot_write(tmp_path, monkeypatch):
    path = tmp_path / "viewer.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        init_target_db(bind=engine)
        monkeypatch.setattr(sql_web, "DATABASE_URL", f"sqlite:///{path}")
        sql_web.initialize_sql_web()

        app = FastAPI()
        app.mount("/sql", WSGIMiddleware(sql_web.sql_web_app))
        with TestClient(app) as client:
            home = client.get("/sql/")
            assert home.status_code == 200
            assert "llm_prompt_audit" in home.text
            assert "read-only" in home.text
            assert client.get("/sql/query/").status_code == 200
            assert client.get("/sql/static/css/bootstrap.min.css").status_code == 200
            query = client.post("/sql/query/", data={
                "sql": "SELECT name FROM sqlite_master WHERE name = 'llm_prompt_audit'",
            })
            assert query.status_code == 200
            assert "llm_prompt_audit" in query.text
            client.post("/sql/query/", data={
                "sql": "CREATE TABLE forbidden_write (id INTEGER PRIMARY KEY)",
            })
        with engine.connect() as db:
            assert db.scalar(text(
                "SELECT name FROM sqlite_master WHERE name = 'forbidden_write'"
            )) is None
    finally:
        engine.dispose()


def test_sql_browser_requires_existing_sqlite_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sql_web, "DATABASE_URL", "postgresql://example.invalid/db")
    try:
        sql_web.initialize_sql_web()
    except RuntimeError as error:
        assert "SQLite" in str(error)
    else:
        raise AssertionError("Non-SQLite database was accepted")
    monkeypatch.setattr(sql_web, "DATABASE_URL", f"sqlite:///{tmp_path / 'missing.db'}")
    try:
        sql_web.initialize_sql_web()
    except RuntimeError as error:
        assert "unavailable" in str(error)
    else:
        raise AssertionError("Missing database was accepted")
