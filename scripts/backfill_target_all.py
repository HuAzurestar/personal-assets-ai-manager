"""Run and verify the complete additive target-schema shadow migration."""
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal, ensure_target_schema  # noqa: E402
from app.services.target_shadow_migration_service import (  # noqa: E402
    TargetShadowMigrationService,
)


def main() -> int:
    ensure_target_schema()
    with SessionLocal() as db:
        report = TargetShadowMigrationService(db).backfill_and_compare()
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.matched else 1


if __name__ == "__main__":
    raise SystemExit(main())
