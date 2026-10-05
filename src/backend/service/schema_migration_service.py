"""Create and validate a new migrated copy, leaving the source untouched."""
from __future__ import annotations

import sqlite3
import shutil
import time
import re
from contextlib import closing
from pathlib import Path

from backend.core.target_database import SQL_ASSET_DIR, TARGET_TABLE_NAMES
from backend.mapper.schema_migration_mapper import NEW_TABLES, SchemaMigrationMapper, schema_profile, expected_schema, fingerprint
from backend.core.stored_timestamp import check_timestamps


def verify_ready(path: Path, report: dict) -> dict:
    """Read-only pre-switch check; any new write invalidates this old report."""
    path = path.resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as connection:
        connection.execute('BEGIN')
        if connection.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('MIGRATION_INTEGRITY_FAILED')
        profile = schema_profile(connection)
        if profile != expected_schema(SQL_ASSET_DIR,TARGET_TABLE_NAMES) or fingerprint(profile) != report.get('schema_fingerprint'):
            raise ValueError('SCHEMA_MANIFEST_MISMATCH')
        check_timestamps(connection,TARGET_TABLE_NAMES)
        manifest = SchemaMigrationMapper(connection).manifest()[1]
        if fingerprint(manifest) != report.get('output_manifest'):
            raise ValueError('MIGRATION_MANIFEST_MISMATCH')
    return dict(ready=True,schema='PIRC-35')


def migrate_copy(source: Path, destination: Path, *, expected_source_manifest=None, candidate_sha=None, seconds=120) -> dict:
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if source == destination or destination.exists():
        raise ValueError("NEW_DESTINATION_REQUIRED")
    if candidate_sha is not None and (not isinstance(candidate_sha,str) or re.fullmatch('[0-9a-f]{40}',candidate_sha) is None):
        raise ValueError('CANDIDATE_SHA_REQUIRED')
    if type(seconds) not in (int,float) or not 0 < seconds <= 600:
        raise ValueError('MIGRATION_BUDGET_INVALID')
    deadline = time.monotonic()+seconds
    profile = expected_schema(SQL_ASSET_DIR,TARGET_TABLE_NAMES)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as original:
        size = original.execute('PRAGMA page_count').fetchone()[0] * original.execute('PRAGMA page_size').fetchone()[0]
    if shutil.disk_usage(destination.parent).free < 2*size+16*1024*1024:
        raise ValueError('MIGRATION_DISK_BUDGET')
    def budget(*_):
        if time.monotonic() > deadline:
            raise ValueError('MIGRATION_TIMEOUT')
    # Reserve only this exact new file; a caller can never overwrite a live DB.
    with destination.open("xb"):
        pass
    preserve_candidate = False
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(destination)) as candidate:
                original.backup(candidate,pages=400,progress=budget,sleep=.025)  # Includes committed WAL; bounded backup steps.
                candidate.set_progress_handler(lambda: int(time.monotonic()>deadline),1000)
                mapper = SchemaMigrationMapper(candidate)
                defaults = mapper.preflight()
                input_manifest = fingerprint(mapper.manifest()[1])
                if expected_source_manifest is not None and input_manifest != expected_source_manifest:
                    raise ValueError('MIGRATION_MANIFEST_MISMATCH')
                gaps = mapper.coverage_gaps()
                columns, before = mapper.manifest(defaults=defaults)
                try:
                    mapper.migrate(SQL_ASSET_DIR, defaults, TARGET_TABLE_NAMES)
                    ref_count = mapper.initialize_reliable_refs()
                    _, after = mapper.manifest(mapped=True, original=columns)
                    if before != after:
                        raise ValueError("MIGRATION_CONSERVATION_FAILED")
                    if any(candidate.execute(f'SELECT COUNT(id) FROM "{table}"').fetchone()[0] for table in NEW_TABLES if table != 'ledger_account_ref'):
                        raise ValueError("MIGRATION_UNEXPECTED_BUSINESS_ROWS")
                    if schema_profile(candidate) != profile:
                        raise ValueError('SCHEMA_MANIFEST_MISMATCH')
                    check_timestamps(candidate,TARGET_TABLE_NAMES)
                    if mapper.coverage_gaps(mapped=True) != gaps:
                        raise ValueError('MIGRATION_CONSERVATION_FAILED')
                    output_manifest = fingerprint(mapper.manifest()[1])
                    budget()
                    if candidate.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                        raise ValueError("MIGRATION_INTEGRITY_FAILED")
                    # A committed (or uncertain-commit) candidate may acquire
                    # new writes. Never delete it because a later READY probe
                    # fails; refuse reuse and require a fresh destination.
                    preserve_candidate = True
                    candidate.commit()
                except BaseException:
                    candidate.rollback()
                    raise
        report = {"ready": True, "preserved_tables": len(before), "verified_defaults": len(defaults),
            "new_tables": len(NEW_TABLES), "schema": "PIRC-35",'coverage_gaps':gaps,'initialized_refs':ref_count,
            'input_manifest':input_manifest,'output_manifest':output_manifest,'schema_fingerprint':fingerprint(profile),
            'normalized_timestamps':mapper.timestamp_changes}
        if candidate_sha is not None:
            report['candidate_sha'] = candidate_sha
        verify_ready(destination,report)
        return report
    except BaseException:
        # This exact file was created by this call and never replaced the source.
        if not preserve_candidate:
            destination.unlink(missing_ok=True)
        raise
