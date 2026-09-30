"""Optional, read-only SQLite inspection UI for local operator deployments."""

from __future__ import annotations

from pathlib import Path

from sqlite_web.sqlite_web import app as sql_web_app, initialize_app

from backend.core.config import DATABASE_URL


def initialize_sql_web() -> None:
    if not DATABASE_URL.startswith("sqlite:///"):
        raise RuntimeError("The SQL browser requires a file-backed SQLite database")
    path = Path(DATABASE_URL.removeprefix("sqlite:///"))
    if not path.is_file():
        raise RuntimeError("The SQL browser database is unavailable")

    # sqlite-web uses SQLite's mode=ro, not a UI-only write prohibition.
    # Do not enable upload, filesystem browsing, extensions or write mode.
    sql_web_app.config.update(ENABLE_LOAD=False, ENABLE_FILESYSTEM=False)
    initialize_app([str(path)], read_only=True)
