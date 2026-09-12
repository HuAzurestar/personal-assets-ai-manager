from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import asc, case, desc, exists, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from app.models.target import (
    BillFact,
    BillRaw,
    ImportFile,
    LedgerEntry,
    LedgerEntrySource,
    LedgerEntryTag,
    ReviewCase,
    ReviewCaseBill,
    ReviewHistory,
    TargetTag,
    TargetTagView,
)
from app.schemas.target_ledger import (
    TargetFactEvidenceVO,
    TargetImportFileVO,
    TargetLedgerAggregateVO,
    TargetLedgerDetailVO,
    TargetLedgerEntryVO,
    TargetLedgerPageQuery,
    TargetLedgerPageVO,
    TargetLedgerSummaryQuery,
    TargetLedgerTagVO,
    TargetRawEvidenceVO,
    TargetReviewEvidenceVO,
    TargetReviewHistoryVO,
    TargetReviewLineVO,
)


class TargetLedgerMapper:
    """Read the hot target projection with bounded explicit-column SQL."""

    def __init__(self, db: Session):
        self.db = db

    def page(self, query: TargetLedgerPageQuery) -> TargetLedgerPageVO:
        clauses = self._page_clauses(query)
        order = asc if query.sort_order == "asc" else desc
        total = self.db.scalar(select(func.count(LedgerEntry.id)).where(*clauses)) or 0
        rows = self.db.execute(select(
            LedgerEntry.id,
            LedgerEntry.ledger_type,
            LedgerEntry.allocation_status,
            LedgerEntry.title,
            LedgerEntry.start_time,
            LedgerEntry.end_time,
            LedgerEntry.in_amount_value,
            LedgerEntry.in_amount_scale,
            LedgerEntry.in_currency_code,
            LedgerEntry.out_amount_value,
            LedgerEntry.out_amount_scale,
            LedgerEntry.out_currency_code,
            LedgerEntry.in_account_code,
            LedgerEntry.out_account_code,
            LedgerEntry.projection_version,
        ).where(*clauses).order_by(
            order(LedgerEntry.start_time), order(LedgerEntry.id),
        ).offset(
            (query.page - 1) * query.page_size
        ).limit(query.page_size)).mappings().all()
        ledger_ids = [row["id"] for row in rows]
        tags = self._tags(ledger_ids)
        return TargetLedgerPageVO(
            items=tuple(self._entry_vo(row, tags.get(row["id"], ())) for row in rows),
            total=total,
            page=query.page,
            page_size=query.page_size,
            filters={
                "date_from": str(query.date_from) if query.date_from else None,
                "date_to": str(query.date_to) if query.date_to else None,
                "ledger_type": list(query.ledger_type),
                "allocation_status": list(query.allocation_status),
                "currency_code": list(query.currency_code),
                "q": query.q,
                "tag": [f"{view}:{tag}" for view, tag in query.tag],
            },
        )

    def detail(self, ledger_id: int) -> TargetLedgerDetailVO | None:
        row = self.db.execute(select(
            LedgerEntry.id,
            LedgerEntry.ledger_type,
            LedgerEntry.allocation_status,
            LedgerEntry.title,
            LedgerEntry.start_time,
            LedgerEntry.end_time,
            LedgerEntry.in_amount_value,
            LedgerEntry.in_amount_scale,
            LedgerEntry.in_currency_code,
            LedgerEntry.out_amount_value,
            LedgerEntry.out_amount_scale,
            LedgerEntry.out_currency_code,
            LedgerEntry.in_account_code,
            LedgerEntry.out_account_code,
            LedgerEntry.projection_version,
        ).where(LedgerEntry.id == ledger_id)).mappings().one_or_none()
        if row is None:
            return None
        entry = self._entry_vo(row, self._tags([ledger_id]).get(ledger_id, ()))
        source_rows = self.db.execute(select(
            LedgerEntrySource.source_kind,
            LedgerEntrySource.source_id,
        ).where(LedgerEntrySource.ledger_id == ledger_id).order_by(
            LedgerEntrySource.source_kind, LedgerEntrySource.source_id,
        )).mappings().all()
        fact_ids = [item["source_id"] for item in source_rows if item["source_kind"] == "BILL_FACT"]
        projection_case_ids = {
            item["source_id"] for item in source_rows if item["source_kind"] == "REVIEW_CASE"
        }
        facts = self._facts(fact_ids)
        raws = self._raw_evidence(fact_ids)
        import_files = self._import_files([item.import_file_id for item in raws])
        related_case_ids = set(self.db.scalars(select(ReviewCaseBill.case_id).where(
            ReviewCaseBill.bill_id.in_(fact_ids)
        ).distinct()).all()) if fact_ids else set()
        case_ids = sorted(projection_case_ids | related_case_ids)
        reviews = self._reviews(case_ids, projection_case_ids)
        return TargetLedgerDetailVO(
            entry=entry,
            facts=facts,
            raw_evidence=raws,
            import_files=import_files,
            reviews=reviews,
        )

    @staticmethod
    def _entry_vo(row, tags) -> TargetLedgerEntryVO:
        return TargetLedgerEntryVO(
            id=row["id"],
            ledger_type=row["ledger_type"],
            allocation_status=row["allocation_status"],
            title=row["title"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            in_amount_value=row["in_amount_value"],
            in_amount_scale=row["in_amount_scale"],
            in_currency_code=row["in_currency_code"],
            out_amount_value=row["out_amount_value"],
            out_amount_scale=row["out_amount_scale"],
            out_currency_code=row["out_currency_code"],
            in_account_code=row["in_account_code"],
            out_account_code=row["out_account_code"],
            projection_version=row["projection_version"],
            tags=tuple(tags),
        )

    def _page_clauses(self, query: TargetLedgerPageQuery) -> list:
        clauses = []
        if query.date_from:
            clauses.append(LedgerEntry.start_time >= datetime.combine(query.date_from, time.min))
        if query.date_to:
            clauses.append(LedgerEntry.start_time <= datetime.combine(query.date_to, time.max))
        if query.ledger_type:
            clauses.append(LedgerEntry.ledger_type.in_(query.ledger_type))
        if query.allocation_status:
            clauses.append(LedgerEntry.allocation_status.in_(query.allocation_status))
        if query.currency_code:
            clauses.append(or_(
                LedgerEntry.in_currency_code.in_(query.currency_code),
                LedgerEntry.out_currency_code.in_(query.currency_code),
            ))
        if query.q:
            clauses.append(LedgerEntry.title.ilike(f"%{query.q.strip()}%"))
        for view_system_name, tag_system_name in query.tag:
            clauses.append(exists(select(LedgerEntryTag.id).join(
                TargetTag, TargetTag.id == LedgerEntryTag.tag_id,
            ).join(
                TargetTagView, TargetTagView.id == TargetTag.view_id,
            ).where(
                LedgerEntryTag.ledger_id == LedgerEntry.id,
                TargetTagView.system_name == view_system_name,
                TargetTag.system_name == tag_system_name,
                TargetTagView.status == "ACTIVE",
                TargetTag.status == "ACTIVE",
            )))
        return clauses

    def _tags(self, ledger_ids: list[int]) -> dict[int, tuple[TargetLedgerTagVO, ...]]:
        if not ledger_ids:
            return {}
        rows = self.db.execute(select(
            LedgerEntryTag.ledger_id,
            TargetTagView.id.label("view_id"),
            TargetTagView.name.label("view_name"),
            TargetTagView.system_name.label("view_system_name"),
            TargetTag.id.label("tag_id"),
            TargetTag.name.label("tag_name"),
            TargetTag.system_name.label("tag_system_name"),
        ).join(
            TargetTag, TargetTag.id == LedgerEntryTag.tag_id,
        ).join(
            TargetTagView, TargetTagView.id == TargetTag.view_id,
        ).where(
            LedgerEntryTag.ledger_id.in_(ledger_ids),
            TargetTagView.status == "ACTIVE",
            TargetTag.status == "ACTIVE",
        ).order_by(LedgerEntryTag.ledger_id, TargetTagView.id)).mappings().all()
        result: dict[int, list[TargetLedgerTagVO]] = {}
        for row in rows:
            result.setdefault(row["ledger_id"], []).append(TargetLedgerTagVO(
                view_id=row["view_id"],
                view_name=row["view_name"],
                view_system_name=row["view_system_name"],
                tag_id=row["tag_id"],
                tag_name=row["tag_name"],
                tag_system_name=row["tag_system_name"],
            ))
        return {ledger_id: tuple(values) for ledger_id, values in result.items()}

    def _facts(self, fact_ids: list[int]) -> tuple[TargetFactEvidenceVO, ...]:
        if not fact_ids:
            return ()
        rows = self.db.execute(select(
            BillFact.id,
            BillFact.fact_key,
            BillFact.occurred_time,
            BillFact.cash_direction,
            BillFact.amount_value,
            BillFact.amount_scale,
            BillFact.currency_code,
            BillFact.account_code,
            BillFact.counterparty,
            BillFact.summary,
        ).where(BillFact.id.in_(fact_ids)).order_by(BillFact.occurred_time, BillFact.id)).mappings().all()
        return tuple(TargetFactEvidenceVO(**row) for row in rows)

    def _raw_evidence(self, fact_ids: list[int]) -> tuple[TargetRawEvidenceVO, ...]:
        if not fact_ids:
            return ()
        rows = self.db.execute(select(
            BillRaw.id,
            BillRaw.bill_id,
            BillRaw.import_file_id,
            BillRaw.source_row_number,
            BillRaw.source_reference,
            BillRaw.raw_payload,
            BillRaw.raw_hash,
            BillRaw.parse_status,
            BillRaw.issue_code,
            BillRaw.issue_message,
        ).where(BillRaw.bill_id.in_(fact_ids)).order_by(BillRaw.bill_id, BillRaw.id)).mappings().all()
        return tuple(TargetRawEvidenceVO(**row) for row in rows)

    def _import_files(self, import_file_ids: list[int]) -> tuple[TargetImportFileVO, ...]:
        unique_ids = list(dict.fromkeys(import_file_ids))
        if not unique_ids:
            return ()
        rows = self.db.execute(select(
            ImportFile.id,
            ImportFile.source_type,
            ImportFile.institution_code,
            ImportFile.filename,
            ImportFile.file_format,
            ImportFile.sha256,
            ImportFile.period_start,
            ImportFile.period_end,
            ImportFile.status,
        ).where(ImportFile.id.in_(unique_ids)).order_by(ImportFile.id)).mappings().all()
        return tuple(TargetImportFileVO(**row) for row in rows)

    def _reviews(
        self,
        case_ids: list[int],
        projection_case_ids: set[int],
    ) -> tuple[TargetReviewEvidenceVO, ...]:
        if not case_ids:
            return ()
        cases = self.db.execute(select(
            ReviewCase.id,
            ReviewCase.review_type,
            ReviewCase.status,
            ReviewCase.allocation_status,
            ReviewCase.version,
            ReviewCase.title,
            ReviewCase.result_json,
        ).where(ReviewCase.id.in_(case_ids)).order_by(ReviewCase.id)).mappings().all()
        line_rows = self.db.execute(select(
            ReviewCaseBill.id,
            ReviewCaseBill.case_id,
            ReviewCaseBill.bill_id,
            ReviewCaseBill.role,
            ReviewCaseBill.party,
            ReviewCaseBill.amount_value,
            ReviewCaseBill.amount_scale,
            ReviewCaseBill.currency_code,
        ).where(ReviewCaseBill.case_id.in_(case_ids)).order_by(
            ReviewCaseBill.case_id, ReviewCaseBill.id,
        )).mappings().all()
        history_rows = self.db.execute(select(
            ReviewHistory.id,
            ReviewHistory.case_id,
            ReviewHistory.version,
            ReviewHistory.operation,
            ReviewHistory.schema_version,
            ReviewHistory.request_json,
            ReviewHistory.before_json,
            ReviewHistory.after_json,
            ReviewHistory.snapshot_hash,
            ReviewHistory.reverses_history_id,
            ReviewHistory.actor,
            ReviewHistory.reason,
            ReviewHistory.idempotency_key,
            ReviewHistory.created_time,
        ).where(ReviewHistory.case_id.in_(case_ids)).order_by(
            ReviewHistory.case_id, ReviewHistory.version, ReviewHistory.id,
        )).mappings().all()
        lines: dict[int, list[TargetReviewLineVO]] = {}
        histories: dict[int, list[TargetReviewHistoryVO]] = {}
        for item in line_rows:
            lines.setdefault(item["case_id"], []).append(TargetReviewLineVO(**item))
        for item in history_rows:
            histories.setdefault(item["case_id"], []).append(TargetReviewHistoryVO(**item))
        return tuple(TargetReviewEvidenceVO(
            **case,
            is_projection_source=(
                case["id"] in projection_case_ids
                or (
                    case["review_type"] in {"TAG", "ACCOUNT"}
                    and case["status"] == "CONFIRMED"
                )
            ),
            lines=tuple(lines.get(case["id"], ())),
            history=tuple(histories.get(case["id"], ())),
        ) for case in cases)

    def summary(
        self,
        query: TargetLedgerSummaryQuery,
    ) -> tuple[int, int, tuple[TargetLedgerAggregateVO, ...]]:
        clauses = []
        if query.date_from:
            clauses.append(LedgerEntry.start_time >= datetime.combine(query.date_from, time.min))
        if query.date_to:
            clauses.append(LedgerEntry.start_time <= datetime.combine(query.date_to, time.max))

        counts = self.db.execute(select(
            func.count(LedgerEntry.id).label("entry_count"),
            func.coalesce(func.sum(case(
                (LedgerEntry.allocation_status.in_(("PARTIAL", "CONFLICT")), 1),
                else_=0,
            )), 0).label("provisional_count"),
        ).where(*clauses)).mappings().one()

        common = (
            func.date(LedgerEntry.start_time).label("day"),
            LedgerEntry.ledger_type.label("ledger_type"),
            LedgerEntry.allocation_status.label("allocation_status"),
        )
        nettable = (
            LedgerEntry.in_currency_code == LedgerEntry.out_currency_code
        ).label("nettable")
        incoming = select(
            *common,
            literal("IN").label("direction"),
            LedgerEntry.in_currency_code.label("currency_code"),
            LedgerEntry.in_amount_scale.label("amount_scale"),
            func.sum(LedgerEntry.in_amount_value).label("amount_value"),
            func.count(LedgerEntry.id).label("entry_count"),
            nettable,
        ).where(
            *clauses,
            LedgerEntry.in_amount_value != 0,
        ).group_by(
            *common,
            LedgerEntry.in_currency_code,
            LedgerEntry.in_amount_scale,
            nettable,
        )
        outgoing = select(
            *common,
            literal("OUT").label("direction"),
            LedgerEntry.out_currency_code.label("currency_code"),
            LedgerEntry.out_amount_scale.label("amount_scale"),
            func.sum(LedgerEntry.out_amount_value).label("amount_value"),
            func.count(LedgerEntry.id).label("entry_count"),
            nettable,
        ).where(
            *clauses,
            LedgerEntry.out_amount_value != 0,
        ).group_by(
            *common,
            LedgerEntry.out_currency_code,
            LedgerEntry.out_amount_scale,
            nettable,
        )
        rows = self.db.execute(union_all(incoming, outgoing)).mappings().all()
        return (
            counts["entry_count"],
            counts["provisional_count"],
            tuple(TargetLedgerAggregateVO(**row) for row in rows),
        )
