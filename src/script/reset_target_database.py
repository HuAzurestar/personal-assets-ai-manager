"""Destructively recreate an empty SQLite database with only PIRC-9 tables."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from backend.core.target_database import TARGET_TABLE_NAMES, init_target_db  # noqa: E402


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def reset(database: Path) -> tuple[str, ...]:
    database = database.resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{database}")
    try:
        existing = inspect(engine).get_table_names()
        invalid = [name for name in existing if not IDENTIFIER.fullmatch(name)]
        if invalid:
            raise RuntimeError(f"refusing to drop unexpected table names: {invalid}")
        with engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys=OFF"))
            for name in existing:
                connection.execute(text(f'DROP TABLE IF EXISTS "{name}"'))
        init_target_db(bind=engine)
        actual = tuple(sorted(inspect(engine).get_table_names()))
        expected = tuple(sorted(TARGET_TABLE_NAMES))
        if actual != expected:
            raise RuntimeError(f"target reset mismatch: expected={expected}, actual={actual}")
        return actual
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument(
        "--confirm-empty-reset",
        action="store_true",
        help="required acknowledgement that all current SQLite data will be deleted",
    )
    args = parser.parse_args()
    if not args.confirm_empty_reset:
        parser.error("--confirm-empty-reset is required")
    tables = reset(args.database)
    print(f"Reset {args.database.resolve()} with {len(tables)} target tables:")
    print("\n".join(tables))


if __name__ == "__main__":
    main()
