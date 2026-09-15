from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from sqlalchemy.orm import Session

from backend.mapper.target_projection_mapper import TargetProjectionMapper
from backend.schema.target_projection import DefaultProjectionWriteVO
from backend.parser.statement_parser import BANKS
from backend.service.target_tag_projection_service import TargetTagProjectionService
from backend.service.target_account_projection_service import TargetAccountProjectionService


class TargetProjectionService:
    """Rebuild default hot entries for a bounded set of target facts."""

    def __init__(self, db: Session):
        self.mapper = TargetProjectionMapper(db)
        self.tags = TargetTagProjectionService(db)
        self.accounts = TargetAccountProjectionService(db)

    def rebuild_defaults(self, fact_ids: list[int]) -> None:
        fact_ids = list(dict.fromkeys(fact_ids))
        facts = self.mapper.facts(fact_ids)
        accounts = self.accounts.effective(facts)
        evidence_by_fact = defaultdict(list)
        for item in self.mapper.nature_evidence(fact_ids):
            evidence_by_fact[item.bill_id].append(item)
        reviewed = self.mapper.financially_reviewed_fact_ids(fact_ids)
        writes = []
        for fact in facts:
            if fact.id in reviewed:
                continue
            evidence = evidence_by_fact[fact.id]
            nature = self._nature(evidence)
            if nature == "refund":
                ledger_type, allocation_status = "REFUND", "DEFAULT"
            elif nature == "neutral":
                ledger_type, allocation_status = "UNRESOLVED", "PARTIAL"
            else:
                ledger_type = "INCOME" if fact.cash_direction == "IN" else "EXPENSE"
                allocation_status = "DEFAULT"
            incoming = fact.amount_value if fact.cash_direction == "IN" else 0
            outgoing = fact.amount_value if fact.cash_direction == "OUT" else 0
            account_state = accounts[fact.id]
            effective_account = account_state.account_code
            account_in = effective_account if incoming else "UNKNOWN"
            account_out = effective_account if outgoing else "UNKNOWN"
            input_hash = hashlib.sha256(json.dumps({
                "fact": [
                    fact.fact_key,
                    fact.occurred_time.isoformat(),
                    fact.cash_direction,
                    fact.amount_value,
                    fact.amount_scale,
                    fact.currency_code,
                    effective_account,
                    account_state.review_case_id,
                    account_state.review_version,
                    fact.counterparty,
                    fact.summary,
                ],
                "nature": nature,
                "evidence": [item.raw_hash for item in evidence],
            }, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            writes.append(DefaultProjectionWriteVO(
                fact_id=fact.id,
                ledger_type=ledger_type,
                allocation_status=allocation_status,
                title=fact.counterparty or fact.summary,
                start_time=fact.occurred_time,
                end_time=fact.occurred_time,
                in_amount_value=incoming,
                in_amount_scale=fact.amount_scale,
                in_currency_code=fact.currency_code,
                out_amount_value=outgoing,
                out_amount_scale=fact.amount_scale,
                out_currency_code=fact.currency_code,
                in_account_code=account_in,
                out_account_code=account_out,
                input_hash=input_hash,
                created_time=fact.created_time,
                updated_time=fact.updated_time,
            ))
        fact_ledgers = self.mapper.write_defaults(writes)
        self.tags.sync_ledgers(list(fact_ledgers.values()))

    @staticmethod
    def _nature(evidence) -> str:
        if any(item.nature == "refund" for item in evidence):
            return "refund"
        platform = [item.nature for item in evidence if item.source_type not in BANKS]
        if platform:
            return platform[-1]
        if evidence and all(item.nature == "neutral" for item in evidence):
            return "neutral"
        return "ordinary"
