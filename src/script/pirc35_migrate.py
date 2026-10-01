"""Generate a verified upgraded copy; this command never switches the runtime DB."""
import argparse
import json
from pathlib import Path

from backend.service.schema_migration_service import migrate_copy

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(migrate_copy(args.source, args.destination)))
    except ValueError as error:
        parser.exit(1, f"{error}\n")
