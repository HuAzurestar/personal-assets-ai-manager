"""Create and shadow-validate unified Review rows from manual matters."""
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal, ensure_target_schema  # noqa: E402
from app.services.candidate_migration_service import (  # noqa: E402
    CandidateReviewShadowMigrationService,
)
from app.services.account_migration_service import (  # noqa: E402
    AccountReviewShadowMigrationService,
)
from app.services.review_migration_service import (  # noqa: E402
    ReviewMatterShadowMigrationService,
)
from app.services.refund_migration_service import (  # noqa: E402
    RefundReviewShadowMigrationService,
)
from app.services.tag_migration_service import (  # noqa: E402
    TagReviewShadowMigrationService,
)
from app.services.import_issue_migration_service import (  # noqa: E402
    ImportIssueReviewShadowMigrationService,
)


def main() -> int:
    ensure_target_schema()
    with SessionLocal() as db:
        matter_report = ReviewMatterShadowMigrationService(db).backfill_and_compare()
        candidate_report = CandidateReviewShadowMigrationService(db).backfill_and_compare()
        refund_report = RefundReviewShadowMigrationService(db).backfill_and_compare()
        account_report = AccountReviewShadowMigrationService(db).backfill_and_compare()
        tag_report = TagReviewShadowMigrationService(db).backfill_and_compare()
        import_issue_report = ImportIssueReviewShadowMigrationService(db).backfill_and_compare()
    result = {
        "matched": (
            matter_report.matched
            and candidate_report.matched
            and refund_report.matched
            and account_report.matched
            and tag_report.matched
            and import_issue_report.matched
        ),
        "manual_matters": matter_report.model_dump(mode="json"),
        "candidates": candidate_report.model_dump(mode="json"),
        "refunds": refund_report.model_dump(mode="json"),
        "accounts": account_report.model_dump(mode="json"),
        "tags": tag_report.model_dump(mode="json"),
        "import_issues": import_issue_report.model_dump(mode="json"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
