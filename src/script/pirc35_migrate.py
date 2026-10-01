"""Generate a verified upgraded copy; this command never switches the runtime DB."""
import argparse
import json
import sqlite3
from pathlib import Path

from backend.service.schema_migration_service import migrate_copy

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument('--candidate-sha',required=True,help='Fixed reviewed application commit SHA; not a moving branch')
    parser.add_argument('--expected-source-manifest',help='Previously observed source manifest fingerprint')
    args = parser.parse_args()
    try:
        print(json.dumps(migrate_copy(args.source,args.destination,candidate_sha=args.candidate_sha,
            expected_source_manifest=args.expected_source_manifest)))
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    except sqlite3.Error:
        parser.exit(1,'MIGRATION_SQL_FAILURE\n')
    except OSError:
        parser.exit(1,'MIGRATION_IO_FAILURE\n')
    except Exception:
        parser.exit(1,'MIGRATION_FAILED\n')
