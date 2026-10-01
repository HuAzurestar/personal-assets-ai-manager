"""Create and validate a new migrated copy, leaving the source untouched."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from backend.core.target_database import SQL_ASSET_DIR, TARGET_TABLE_NAMES
from backend.mapper.schema_migration_mapper import NEW_TABLES, SchemaMigrationMapper


def migrate_copy(source: Path, destination: Path) -> dict:
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if source == destination or destination.exists():
        raise ValueError("NEW_DESTINATION_REQUIRED")
    # Reserve only this exact new file; a caller can never overwrite a live DB.
    with destination.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(destination)) as candidate:
                original.backup(candidate)  # SQLite backup includes committed WAL content.
                mapper = SchemaMigrationMapper(candidate)
                defaults = mapper.preflight()
                columns, before = mapper.manifest(defaults=defaults)
                try:
                    mapper.migrate(SQL_ASSET_DIR, defaults, TARGET_TABLE_NAMES)
                    _, after = mapper.manifest(mapped=True, original=columns)
                    if before != after:
                        raise ValueError("MIGRATION_CONSERVATION_FAILED")
                    if any(candidate.execute(f'SELECT COUNT(id) FROM "{table}"').fetchone()[0] for table in NEW_TABLES):
                        raise ValueError("MIGRATION_UNEXPECTED_BUSINESS_ROWS")
                    if candidate.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                        raise ValueError("MIGRATION_INTEGRITY_FAILED")
                    candidate.commit()
                except BaseException:
                    candidate.rollback()
                    raise
        return {"ready": True, "preserved_tables": len(before), "verified_defaults": len(defaults),
                "new_tables": len(NEW_TABLES), "schema": "PIRC-35"}
    except BaseException:
        # This exact file was created by this call and never replaced the source.
        destination.unlink(missing_ok=True)
        raise
