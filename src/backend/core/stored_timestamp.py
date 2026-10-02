"""Strict equivalent UTC text for offline migration; never invent a time."""
from datetime import datetime, timezone
import re

_UTC = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3}(?:\d{3})?)?Z',re.ASCII)


def canonical_timestamp(value):
    if not isinstance(value,str) or _UTC.fullmatch(value) is None:
        raise ValueError('TIMESTAMP_REVIEW_REQUIRED')
    try:
        parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError as error:
        raise ValueError('TIMESTAMP_REVIEW_REQUIRED') from error
    return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')


def check_timestamps(connection, tables, *, normalize=False):
    """Each short batch <=400; all repairs occur only inside a copy transaction."""
    changed = 0
    for table in tables:
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
        for column in sorted(columns & {'created_time','updated_time','occurred_time'}):
            cursor = connection.execute(f'SELECT id,"{column}" FROM "{table}" ORDER BY id')
            while rows := cursor.fetchmany(400):
                updates = []
                for identifier,value in rows:
                    canonical = canonical_timestamp(value)
                    if canonical != value:
                        if not normalize:
                            raise ValueError('TIMESTAMP_MIGRATION_REQUIRED')
                        updates.append((canonical,identifier))
                if updates:
                    connection.executemany(f'UPDATE "{table}" SET "{column}"=? WHERE id=?',updates)
                    changed += len(updates)
    return changed
