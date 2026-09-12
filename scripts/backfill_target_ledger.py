"""Create and shadow-validate enhanced ledger lookup/projection data."""
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal, ensure_target_schema  # noqa: E402
from app.services.tag_dictionary_migration_service import (  # noqa: E402
    TagDictionaryShadowMigrationService,
)
from app.services.ledger_projection_migration_service import (  # noqa: E402
    LedgerProjectionShadowMigrationService,
)
from app.services.ledger_summary_comparison_service import (  # noqa: E402
    LedgerSummaryShadowComparisonService,
)


def main() -> int:
    ensure_target_schema()
    with SessionLocal() as db:
        tag_report = TagDictionaryShadowMigrationService(db).backfill_and_compare()
        ledger_report = LedgerProjectionShadowMigrationService(db).backfill_and_compare()
        summary_report = LedgerSummaryShadowComparisonService(db).compare()
    result = {
        "matched": tag_report.matched and ledger_report.matched and summary_report.matched,
        "tag_dictionary": tag_report.model_dump(mode="json"),
        "ledger_projection": ledger_report.model_dump(mode="json"),
        "summary_comparison": summary_report.model_dump(mode="json"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
