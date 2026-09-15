from __future__ import annotations

import json
from dataclasses import asdict

from sqlalchemy.orm import Session

from backend.error import MultipleTagsForView, UnknownTagSelector
from backend.mapper.target_ledger_mapper import TargetLedgerMapper
from backend.schema.target_ledger import (
    TargetFactEvidenceRead,
    TargetImportFileRead,
    TargetLedgerDetailRead,
    TargetLedgerEntryRead,
    TargetLedgerEntryVO,
    TargetLedgerPageQuery,
    TargetLedgerPageRead,
    TargetLedgerTagRead,
    TargetMoneyRead,
    TargetRawEvidenceRead,
    TargetReviewEvidenceRead,
    TargetReviewHistoryRead,
    TargetReviewLineRead,
)


class TargetLedgerService:
    """Ledger list/detail use cases over the hot projection."""

    def __init__(self, db: Session):
        self.mapper = TargetLedgerMapper(db)

    def page(self, query: TargetLedgerPageQuery) -> TargetLedgerPageRead:
        self._validate_query(query)
        page = self.mapper.page(query)
        return TargetLedgerPageRead(
            items=[self._entry(item) for item in page.items],
            total=page.total,
            page=page.page,
            page_size=page.page_size,
            filters=page.filters,
        )

    def detail(self, ledger_id: int) -> TargetLedgerDetailRead | None:
        detail = self.mapper.detail(ledger_id)
        if detail is None:
            return None
        return TargetLedgerDetailRead(
            entry=self._entry(detail.entry),
            facts=[TargetFactEvidenceRead(
                id=fact.id,
                fact_key=fact.fact_key,
                occurred_time=fact.occurred_time,
                cash_direction=fact.cash_direction,
                amount=TargetMoneyRead(
                    amount_value=fact.amount_value,
                    amount_scale=fact.amount_scale,
                    currency_code=fact.currency_code,
                ),
                account_code=fact.account_code,
                counterparty=fact.counterparty,
                summary=fact.summary,
            ) for fact in detail.facts],
            raw_evidence=[TargetRawEvidenceRead(
                id=raw.id,
                bill_id=raw.bill_id,
                import_file_id=raw.import_file_id,
                source_row_number=raw.source_row_number,
                source_reference=raw.source_reference,
                raw_payload=self._json_value(raw.raw_payload),
                raw_hash=raw.raw_hash,
                parse_status=raw.parse_status,
                issue_code=raw.issue_code,
                issue_message=raw.issue_message,
            ) for raw in detail.raw_evidence],
            import_files=[TargetImportFileRead(**asdict(item)) for item in detail.import_files],
            reviews=[TargetReviewEvidenceRead(
                id=review.id,
                review_type=review.review_type,
                status=review.status,
                allocation_status=review.allocation_status,
                version=review.version,
                title=review.title,
                result=self._json_object(review.result_json),
                is_projection_source=review.is_projection_source,
                lines=[TargetReviewLineRead(
                    id=line.id,
                    bill_id=line.bill_id,
                    role=line.role,
                    party=line.party,
                    amount=TargetMoneyRead(
                        amount_value=line.amount_value,
                        amount_scale=line.amount_scale,
                        currency_code=line.currency_code,
                    ),
                ) for line in review.lines],
                history=[TargetReviewHistoryRead(
                    id=history.id,
                    version=history.version,
                    operation=history.operation,
                    schema_version=history.schema_version,
                    request=self._json_object(history.request_json),
                    before=self._json_object(history.before_json),
                    after=self._json_object(history.after_json),
                    snapshot_hash=history.snapshot_hash,
                    reverses_history_id=history.reverses_history_id,
                    actor=history.actor,
                    reason=history.reason,
                    idempotency_key=history.idempotency_key,
                    created_time=history.created_time,
                ) for history in review.history],
            ) for review in detail.reviews],
        )

    @staticmethod
    def _entry(item: TargetLedgerEntryVO) -> TargetLedgerEntryRead:
        return TargetLedgerEntryRead(
            id=item.id,
            ledger_type=item.ledger_type,
            allocation_status=item.allocation_status,
            title=item.title,
            start_time=item.start_time,
            end_time=item.end_time,
            incoming=TargetMoneyRead(
                amount_value=item.in_amount_value,
                amount_scale=item.in_amount_scale,
                currency_code=item.in_currency_code,
            ),
            outgoing=TargetMoneyRead(
                amount_value=item.out_amount_value,
                amount_scale=item.out_amount_scale,
                currency_code=item.out_currency_code,
            ),
            in_account_code=item.in_account_code,
            out_account_code=item.out_account_code,
            projection_version=item.projection_version,
            tags=[TargetLedgerTagRead(**asdict(tag)) for tag in item.tags],
        )

    @staticmethod
    def _json_value(value: str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    @classmethod
    def _json_object(cls, value: str) -> dict:
        parsed = cls._json_value(value)
        return parsed if isinstance(parsed, dict) else {"_value": parsed}

    @staticmethod
    def parse_tag_selectors(values: list[str]) -> tuple[tuple[str, str], ...]:
        result = []
        for value in values:
            try:
                view, tag = value.split(":", 1)
            except ValueError as error:
                raise UnknownTagSelector(
                    "tag must use view_system_name:tag_system_name"
                ) from error
            if not view or not tag:
                raise UnknownTagSelector(
                    "tag must use view_system_name:tag_system_name"
                )
            result.append((view, tag))
        return tuple(result)

    @staticmethod
    def _validate_query(query: TargetLedgerPageQuery) -> None:
        selected = {}
        for view, tag in query.tag:
            previous = selected.get(view)
            if previous is not None and previous != tag:
                raise MultipleTagsForView("only one tag may be selected in each view")
            selected[view] = tag
