from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.mappers.dashboard_mapper import DashboardMapper
from app.money import cents, money
from app.schemas.ledger import LedgerPageQuery


class DashboardService:
    """Compute the published summary from one set-based Mapper snapshot."""

    def __init__(self, db: Session):
        self.mapper = DashboardMapper(db)

    def summary(self, query: LedgerPageQuery) -> dict:
        data = self.mapper.load(query)
        matter_lines: dict[int, list[dict]] = {}
        for matter in data.matters:
            for line in matter.lines:
                matter_lines.setdefault(line["bill_id"], []).append({
                    **line,
                    "matter_id": matter.id,
                })
        refunds_by_bill: dict[int, int] = {}
        for allocation in data.allocations:
            refunds_by_bill[allocation.refund_bill_id] = (
                refunds_by_bill.get(allocation.refund_bill_id, 0) + cents(allocation.amount)
            )

        contributions = []
        trend: dict[str, dict[str, int]] = {}
        income_total = spending_total = refund_total = unallocated_total = unresolved_total = 0
        for bill in data.bills:
            amount = cents(bill.amount)
            lines = matter_lines.get(bill.id, [])
            income = amount if amount > 0 and bill.id not in data.refund_bill_ids else 0
            spending = amount if amount < 0 else 0
            unresolved = 0
            if lines:
                income = sum(line["amount_cents"] for line in lines if line["role"] == "income")
                spending = -sum(line["amount_cents"] for line in lines if line["role"] == "expense")
                unresolved = abs(amount) - sum(line["amount_cents"] for line in lines)
            allocated = refunds_by_bill.get(bill.id, 0)
            unallocated = amount - allocated if bill.id in data.refund_bill_ids else 0
            contribution = {
                "bill_id": bill.id,
                "merchant": bill.merchant,
                "occurred_at": bill.occurred_at.isoformat(),
                "income": money(income),
                "spending": money(spending),
                "refund_offset": money(allocated),
                "net": money(income + spending + allocated),
                "cash_amount": bill.amount,
                "unallocated_refund": money(unallocated),
                "unresolved_amount": money(unresolved),
                "matter_ids": sorted({line["matter_id"] for line in lines}),
            }
            contributions.append(contribution)
            income_total += income
            spending_total += spending
            refund_total += allocated
            unallocated_total += unallocated
            unresolved_total += unresolved
            day = str(bill.occurred_at.date())
            point = trend.setdefault(day, {
                "income": 0,
                "spending": 0,
                "refund_offset": 0,
                "net": 0,
                "bill_count": 0,
            })
            point["income"] += income
            point["spending"] += spending
            point["refund_offset"] += allocated
            point["net"] += income + spending + allocated
            point["bill_count"] += 1

        warnings = self._review_warnings(data.warning_candidates)
        unreviewed_count = sum(
            1 for bill in data.bills
            if bill.id not in matter_lines and bill.id not in data.refund_bill_ids
        )
        bill_ids = [bill.id for bill in data.bills]
        totals = {
            "income": money(income_total),
            "spending": money(spending_total),
            "refund_offset": money(refund_total),
            "net": money(income_total + spending_total + refund_total),
            "unallocated_refund": money(unallocated_total),
            "unresolved_amount": money(unresolved_total),
        }
        filters = {
            "date_from": str(query.date_from) if query.date_from else None,
            "date_to": str(query.date_to) if query.date_to else None,
            "amount_min": query.amount_min,
            "amount_max": query.amount_max,
            "source": list(query.source),
            "account": list(query.account),
            "direction": query.direction,
            "q": query.q,
            "tag": list(query.tag),
        }
        return {
            **totals,
            "cash_net": money(sum(cents(amount) for amount in data.cash_amounts)),
            "contributions": contributions,
            "unreviewed_count": unreviewed_count,
            "review_warnings": warnings,
            "provisional": bool(
                warnings or unreviewed_count or unallocated_total or unresolved_total
            ),
            "open_balances": [
                {"matter_id": matter.id, **balance, "amount": money(balance["amount_cents"])}
                for matter in data.matters
                for balance in matter.balances
                if balance["amount_cents"]
            ],
            "issue_count": data.issue_count,
            "bill_count": len(data.bills),
            "effective_count": len(data.bills),
            "transaction_ids": bill_ids,
            "composition": {
                "income": [row["bill_id"] for row in contributions if row["income"]],
                "spending": [row["bill_id"] for row in contributions if row["spending"]],
                "refund_offset": [{
                    "allocation_id": allocation.id,
                    "refund_bill_id": allocation.refund_bill_id,
                    "expense_bill_id": allocation.expense_bill_id,
                    "amount": allocation.amount,
                } for allocation in data.allocations],
                "net": bill_ids,
                "effective_count": bill_ids,
            },
            "import_count": data.import_count,
            "candidate_count": data.candidate_count,
            "transfer_group_count": data.transfer_group_count,
            "trend": [{
                "day": day,
                **{
                    key: money(value) if key != "bill_count" else value
                    for key, value in point.items()
                },
            } for day, point in trend.items()],
            "filters": filters,
            "basis_version": "review-foundation-v2",
            "generated_at": datetime.now().isoformat(),
        }

    @staticmethod
    def _review_warnings(candidates) -> list[dict]:
        warnings = []
        owners: dict[int, int] = {}
        for candidate in candidates:
            if candidate.status in {"third_party_transfer_grouped", "legacy_transfer_excluded"}:
                warnings.append({
                    "candidate_id": candidate.id,
                    "reason": "历史整笔排除缺少新的金额分配依据，请核验后改为手工事项",
                })
            for bill_id in candidate.member_bill_ids:
                if bill_id in owners:
                    warnings.append({
                        "candidate_id": candidate.id,
                        "conflicts_with": owners[bill_id],
                        "bill_id": bill_id,
                        "reason": "历史有效决定重复占用同一流水；请撤销错误决定",
                    })
                owners[bill_id] = candidate.id
        return warnings
