from sqlalchemy.orm import Session

from app.mappers.drilldown_mapper import DrilldownMapper
from app.schemas.ledger import LedgerPageQuery
from app.services.dashboard_service import DashboardService
from app.services.ledger_service import bill_read_from_vo


class DrilldownService:
    def __init__(self, db: Session):
        self.summary_service = DashboardService(db)
        self.mapper = DrilldownMapper(db)

    def report(self, query: LedgerPageQuery) -> dict:
        summary = self.summary_service.summary(query)
        matter_ids = list(dict.fromkeys(
            matter_id
            for contribution in summary["contributions"]
            for matter_id in contribution["matter_ids"]
        ))
        evidence = self.mapper.load(query, summary["transaction_ids"], matter_ids)
        contributions_by_bill = {
            row["bill_id"]: row for row in summary["contributions"]
        }
        transactions = []
        for bill_id in summary["transaction_ids"]:
            bill = evidence.bills[bill_id]
            candidate_ids = evidence.candidate_ids_by_bill.get(bill_id, ())
            transactions.append({
                "bill": bill_read_from_vo(bill),
                "source": evidence.sources[bill_id],
                "tag_audits": evidence.tag_audits.get(bill_id, ()),
                "account_revisions": evidence.account_revisions.get(bill_id, ()),
                "candidate_ids": candidate_ids,
                "candidate_actions": [
                    action
                    for candidate_id in candidate_ids
                    for action in evidence.candidate_actions.get(candidate_id, ())
                ],
                "refund_allocations": evidence.refund_allocations.get(bill_id, ()),
                "refund_nature_audits": evidence.refund_nature_audits.get(bill_id, ()),
                "review_matters": [
                    evidence.review_matters[matter_id]
                    for matter_id in contributions_by_bill[bill_id]["matter_ids"]
                ],
            })
        return {
            "transaction_ids": summary["transaction_ids"],
            "summary": {
                key: summary[key]
                for key in ("income", "spending", "refund_offset", "net", "effective_count")
            },
            "composition": summary["composition"],
            "contributions": summary["contributions"],
            "transactions": transactions,
            "excluded": [{
                "bill_id": bill.id,
                "reason": "confirmed_duplicate" if bill.duplicate_of_id else "confirmed_transfer",
                "source": evidence.sources[bill.id],
                "candidate_actions": [
                    action
                    for candidate_id in evidence.candidate_ids_by_bill.get(bill.id, ())
                    for action in evidence.candidate_actions.get(candidate_id, ())
                ],
            } for bill in evidence.excluded],
            "filters": summary["filters"],
            "basis_version": summary["basis_version"],
            "generated_at": summary["generated_at"],
        }
