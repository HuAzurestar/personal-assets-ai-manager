from __future__ import annotations

import json
from dataclasses import replace

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import (
    AccountRevision,
    Bill,
    CandidateActionLog,
    ImportArtifact,
    ImportBatch,
    LedgerOrigin,
    RefundAllocation,
    RefundAllocationAudit,
    RefundNatureAudit,
    ReviewCandidate,
    ReviewMatter,
    ReviewMatterRevision,
    TagAudit,
)
from app.mappers.ledger_mapper import LedgerMapper
from app.money import money
from app.schemas.dashboard import DrilldownEvidenceVO, ExcludedBillVO
from app.schemas.ledger import LedgerPageQuery


class DrilldownMapper:
    """Batch-load historical evidence for one explicitly requested drilldown."""

    def __init__(self, db: Session):
        self.db = db
        self.ledger = LedgerMapper(db)

    def load(
        self,
        query: LedgerPageQuery,
        transaction_ids: list[int],
        matter_ids: list[int],
    ) -> DrilldownEvidenceVO:
        excluded_query = replace(query, scope="excluded")
        excluded_clauses = self.ledger.filter_clauses(excluded_query)
        excluded_rows = self.db.execute(
            select(Bill.id, Bill.duplicate_of_id)
            .where(*excluded_clauses)
            .order_by(Bill.id)
        ).mappings().all()
        excluded = tuple(ExcludedBillVO(
            id=row["id"],
            duplicate_of_id=row["duplicate_of_id"],
        ) for row in excluded_rows)
        all_bill_ids = list(dict.fromkeys([
            *transaction_ids,
            *(bill.id for bill in excluded),
        ]))
        bills = self.ledger.by_ids(transaction_ids)
        sources = self._sources(all_bill_ids)
        candidate_ids_by_bill, candidate_ids = self._candidate_index(all_bill_ids)
        candidate_actions = self._candidate_actions(candidate_ids)
        tag_audits = self._tag_audits(transaction_ids)
        account_revisions = self._account_revisions(transaction_ids)
        refund_allocations = self._refund_allocations(transaction_ids)
        refund_nature_audits = self._refund_nature_audits(transaction_ids)
        review_matters = self._review_matters(matter_ids)
        return DrilldownEvidenceVO(
            bills=bills,
            excluded=excluded,
            sources=sources,
            candidate_ids_by_bill=candidate_ids_by_bill,
            candidate_actions=candidate_actions,
            tag_audits=tag_audits,
            account_revisions=account_revisions,
            refund_allocations=refund_allocations,
            refund_nature_audits=refund_nature_audits,
            review_matters=review_matters,
        )

    def _sources(self, bill_ids: list[int]) -> dict[int, dict]:
        empty = {
            bill_id: {
                "bill_id": bill_id,
                "origin": None,
                "artifact": None,
                "batch": None,
                "raw_fields": {},
            }
            for bill_id in bill_ids
        }
        if not bill_ids:
            return empty
        origins = self.db.execute(
            select(
                LedgerOrigin.bill_id,
                LedgerOrigin.source_type,
                LedgerOrigin.source_reference,
                LedgerOrigin.source_row_number,
                LedgerOrigin.import_batch_id,
                LedgerOrigin.raw_payload,
            ).where(LedgerOrigin.bill_id.in_(bill_ids))
        ).mappings().all()
        batch_ids = list(dict.fromkeys(
            row["import_batch_id"] for row in origins if row["import_batch_id"]
        ))
        batches = {
            row["id"]: row
            for row in self.db.execute(select(
                ImportBatch.id,
                ImportBatch.filename,
                ImportBatch.imported_at,
            ).where(ImportBatch.id.in_(batch_ids))).mappings().all()
        } if batch_ids else {}
        artifacts = {}
        if batch_ids:
            for row in self.db.execute(select(
                ImportArtifact.id,
                ImportArtifact.import_batch_id,
                ImportArtifact.filename,
                ImportArtifact.file_format,
                ImportArtifact.archive_entry,
                ImportArtifact.sha256,
            ).where(ImportArtifact.import_batch_id.in_(batch_ids)).order_by(ImportArtifact.id)).mappings().all():
                artifacts.setdefault(row["import_batch_id"], row)
        for origin in origins:
            try:
                raw_fields = json.loads(origin["raw_payload"] or "{}")
            except (json.JSONDecodeError, TypeError):
                raw_fields = {"unparsed": origin["raw_payload"]}
            batch = batches.get(origin["import_batch_id"])
            artifact = artifacts.get(origin["import_batch_id"])
            empty[origin["bill_id"]] = {
                "bill_id": origin["bill_id"],
                "origin": {
                    "source_type": origin["source_type"],
                    "source_reference": origin["source_reference"],
                    "source_row_number": origin["source_row_number"],
                    "import_batch_id": origin["import_batch_id"],
                },
                "artifact": ({
                    "filename": artifact["filename"],
                    "file_format": artifact["file_format"],
                    "archive_entry": artifact["archive_entry"],
                    "sha256": artifact["sha256"],
                } if artifact else None),
                "batch": ({
                    "id": batch["id"],
                    "filename": batch["filename"],
                    "imported_at": batch["imported_at"],
                } if batch else None),
                "raw_fields": raw_fields,
            }
        return empty

    def _candidate_index(self, bill_ids: list[int]) -> tuple[dict[int, tuple[int, ...]], list[int]]:
        if not bill_ids:
            return {}, []
        wanted = set(bill_ids)
        rows = self.db.execute(
            select(
                ReviewCandidate.id,
                ReviewCandidate.bill_id,
                ReviewCandidate.related_bill_id,
                ReviewCandidate.member_bill_ids,
            )
            .where(ReviewCandidate.status != "superseded_duplicate_group")
            .order_by(ReviewCandidate.id)
        ).mappings().all()
        index: dict[int, list[int]] = {}
        candidate_ids = []
        for row in rows:
            try:
                stored = json.loads(row["member_bill_ids"] or "[]")
            except (json.JSONDecodeError, TypeError):
                stored = []
            if not isinstance(stored, list):
                stored = []
            members = tuple(dict.fromkeys([
                *(int(value) for value in stored),
                row["bill_id"],
                row["related_bill_id"],
            ]))
            relevant = wanted.intersection(members)
            if relevant:
                candidate_ids.append(row["id"])
                for bill_id in relevant:
                    index.setdefault(bill_id, []).append(row["id"])
        return {bill_id: tuple(ids) for bill_id, ids in index.items()}, candidate_ids

    def _candidate_actions(self, candidate_ids: list[int]) -> dict[int, tuple[dict, ...]]:
        if not candidate_ids:
            return {}
        rows = self.db.execute(select(
            CandidateActionLog.id,
            CandidateActionLog.candidate_id,
            CandidateActionLog.action,
            CandidateActionLog.actor,
            CandidateActionLog.reason,
            CandidateActionLog.before_state,
            CandidateActionLog.after_state,
            CandidateActionLog.reverses_action_id,
            CandidateActionLog.idempotency_key,
            CandidateActionLog.undone,
            CandidateActionLog.undone_at,
            CandidateActionLog.created_at,
        ).where(CandidateActionLog.candidate_id.in_(candidate_ids)).order_by(
            CandidateActionLog.candidate_id,
            CandidateActionLog.created_at,
            CandidateActionLog.id,
        )).mappings().all()
        result: dict[int, list[dict]] = {}
        for row in rows:
            result.setdefault(row["candidate_id"], []).append({
                "id": row["id"],
                "candidate_id": row["candidate_id"],
                "action": row["action"],
                "actor": row["actor"],
                "reason": row["reason"],
                "before_state": json.loads(row["before_state"]),
                "after_state": json.loads(row["after_state"]) if row["after_state"] else None,
                "reverses_action_id": row["reverses_action_id"],
                "idempotency_key": row["idempotency_key"],
                "undone": row["undone"],
                "undone_at": row["undone_at"],
                "created_at": row["created_at"],
            })
        return {candidate_id: tuple(actions) for candidate_id, actions in result.items()}

    def _tag_audits(self, bill_ids: list[int]) -> dict[int, tuple[dict, ...]]:
        if not bill_ids:
            return {}
        rows = self.db.execute(select(
            TagAudit.id,
            TagAudit.bill_id,
            TagAudit.category,
            TagAudit.tags,
            TagAudit.strategy,
            TagAudit.confidence,
            TagAudit.provider,
            TagAudit.superseded,
            TagAudit.action,
            TagAudit.actor,
            TagAudit.reason,
            TagAudit.reverses_audit_id,
            TagAudit.undone,
            TagAudit.undone_at,
            TagAudit.created_at,
            TagAudit.tag_state_json,
        ).where(TagAudit.bill_id.in_(bill_ids)).order_by(
            TagAudit.bill_id, TagAudit.created_at, TagAudit.id,
        )).mappings().all()
        result: dict[int, list[dict]] = {}
        for row in rows:
            try:
                state = json.loads(row["tag_state_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                state = {}
            result.setdefault(row["bill_id"], []).append({
                "id": row["id"],
                "category": row["category"],
                "tags": [tag for tag in row["tags"].split(",") if tag],
                "strategy": row["strategy"],
                "confidence": row["confidence"],
                "provider": row["provider"],
                "superseded": row["superseded"],
                "action": row["action"],
                "actor": row["actor"],
                "reason": row["reason"],
                "reverses_audit_id": row["reverses_audit_id"],
                "undone": row["undone"],
                "undone_at": row["undone_at"],
                "created_at": row["created_at"],
                "tag_state": state if isinstance(state, dict) else {},
            })
        return {bill_id: tuple(audits) for bill_id, audits in result.items()}

    def _account_revisions(self, bill_ids: list[int]) -> dict[int, tuple[dict, ...]]:
        if not bill_ids:
            return {}
        rows = self.db.execute(select(
            AccountRevision.id,
            AccountRevision.bill_id,
            AccountRevision.before_account,
            AccountRevision.after_account,
            AccountRevision.action,
            AccountRevision.actor,
            AccountRevision.reason,
            AccountRevision.reverses_revision_id,
            AccountRevision.undone,
            AccountRevision.undone_at,
            AccountRevision.created_at,
        ).where(AccountRevision.bill_id.in_(bill_ids)).order_by(
            AccountRevision.bill_id, AccountRevision.created_at, AccountRevision.id,
        )).mappings().all()
        result: dict[int, list[dict]] = {}
        for row in rows:
            result.setdefault(row["bill_id"], []).append({
                key: row[key] for key in (
                    "id", "before_account", "after_account", "action", "actor", "reason",
                    "reverses_revision_id", "undone", "undone_at", "created_at",
                )
            })
        return {bill_id: tuple(revisions) for bill_id, revisions in result.items()}

    def _refund_allocations(self, bill_ids: list[int]) -> dict[int, tuple[dict, ...]]:
        if not bill_ids:
            return {}
        rows = self.db.execute(select(
            RefundAllocation.id,
            RefundAllocation.refund_bill_id,
            RefundAllocation.expense_bill_id,
            RefundAllocation.amount,
            RefundAllocation.status,
            RefundAllocation.idempotency_key,
            RefundAllocation.created_at,
            RefundAllocation.revoked_at,
        ).where(
            or_(
                RefundAllocation.refund_bill_id.in_(bill_ids),
                RefundAllocation.expense_bill_id.in_(bill_ids),
            ),
            RefundAllocation.status == "confirmed",
        ).order_by(RefundAllocation.id)).mappings().all()
        allocation_ids = [row["id"] for row in rows]
        audit_rows = self.db.execute(select(
            RefundAllocationAudit.id,
            RefundAllocationAudit.allocation_id,
            RefundAllocationAudit.action,
            RefundAllocationAudit.actor,
            RefundAllocationAudit.reason,
            RefundAllocationAudit.before_state,
            RefundAllocationAudit.after_state,
            RefundAllocationAudit.reverses_audit_id,
            RefundAllocationAudit.idempotency_key,
            RefundAllocationAudit.created_at,
        ).where(RefundAllocationAudit.allocation_id.in_(allocation_ids)).order_by(
            RefundAllocationAudit.allocation_id, RefundAllocationAudit.id,
        )).mappings().all() if allocation_ids else []
        audits: dict[int, list[dict]] = {}
        for row in audit_rows:
            audits.setdefault(row["allocation_id"], []).append({
                "id": row["id"],
                "action": row["action"],
                "actor": row["actor"],
                "reason": row["reason"],
                "before_state": json.loads(row["before_state"]),
                "after_state": json.loads(row["after_state"]),
                "reverses_audit_id": row["reverses_audit_id"],
                "idempotency_key": row["idempotency_key"],
                "created_at": row["created_at"],
            })
        result: dict[int, list[dict]] = {}
        for row in rows:
            allocation = {
                "id": row["id"],
                "refund_bill_id": row["refund_bill_id"],
                "expense_bill_id": row["expense_bill_id"],
                "amount": row["amount"],
                "status": row["status"],
                "idempotency_key": row["idempotency_key"],
                "created_at": row["created_at"],
                "revoked_at": row["revoked_at"],
                "audits": audits.get(row["id"], []),
            }
            for bill_id in (row["refund_bill_id"], row["expense_bill_id"]):
                if bill_id in bill_ids:
                    result.setdefault(bill_id, []).append(allocation)
        return {bill_id: tuple(values) for bill_id, values in result.items()}

    def _refund_nature_audits(self, bill_ids: list[int]) -> dict[int, tuple[dict, ...]]:
        if not bill_ids:
            return {}
        rows = self.db.execute(select(
            RefundNatureAudit.id,
            RefundNatureAudit.bill_id,
            RefundNatureAudit.action,
            RefundNatureAudit.reason,
            RefundNatureAudit.actor,
            RefundNatureAudit.before_nature,
            RefundNatureAudit.after_nature,
            RefundNatureAudit.idempotency_key,
            RefundNatureAudit.created_at,
        ).where(RefundNatureAudit.bill_id.in_(bill_ids)).order_by(
            RefundNatureAudit.bill_id, RefundNatureAudit.id,
        )).mappings().all()
        result: dict[int, list[dict]] = {}
        for row in rows:
            result.setdefault(row["bill_id"], []).append({
                key: row[key] for key in (
                    "id", "action", "reason", "actor", "before_nature", "after_nature",
                    "idempotency_key", "created_at",
                )
            })
        return {bill_id: tuple(audits) for bill_id, audits in result.items()}

    def _review_matters(self, matter_ids: list[int]) -> dict[int, dict]:
        if not matter_ids:
            return {}
        matters = {
            row["id"]: row
            for row in self.db.execute(select(
                ReviewMatter.id, ReviewMatter.version,
            ).where(ReviewMatter.id.in_(matter_ids))).mappings().all()
        }
        revisions = self.db.execute(select(
            ReviewMatterRevision.matter_id,
            ReviewMatterRevision.version,
            ReviewMatterRevision.action,
            ReviewMatterRevision.snapshot,
            ReviewMatterRevision.reason,
            ReviewMatterRevision.actor,
            ReviewMatterRevision.created_at,
        ).where(ReviewMatterRevision.matter_id.in_(matter_ids)).order_by(
            ReviewMatterRevision.matter_id, ReviewMatterRevision.version,
        )).mappings().all()
        revisions_by_matter: dict[int, list] = {}
        line_bill_ids = []
        snapshots = {}
        for row in revisions:
            snapshot = json.loads(row["snapshot"])
            snapshots[(row["matter_id"], row["version"])] = snapshot
            revisions_by_matter.setdefault(row["matter_id"], []).append(row)
            line_bill_ids.extend(line["bill_id"] for line in snapshot["lines"])
        line_bills = {
            row["id"]: row
            for row in self.db.execute(select(
                Bill.id, Bill.merchant, Bill.account_name, Bill.occurred_at, Bill.amount,
            ).where(Bill.id.in_(list(dict.fromkeys(line_bill_ids))))).mappings().all()
        } if line_bill_ids else {}
        result = {}
        for matter_id, matter in matters.items():
            rows = revisions_by_matter.get(matter_id, [])
            current = next(row for row in rows if row["version"] == matter["version"])
            snapshot = snapshots[(matter_id, matter["version"])]
            lines = []
            for line in snapshot["lines"]:
                bill = line_bills[line["bill_id"]]
                lines.append({
                    **line,
                    "amount": money(line["amount_cents"]),
                    "merchant": bill["merchant"],
                    "account_name": bill["account_name"],
                    "occurred_at": bill["occurred_at"],
                    "bill_amount": bill["amount"],
                })
            result[matter_id] = {
                **snapshot,
                "lines": lines,
                "id": matter_id,
                "version": matter["version"],
                "status": "revoked" if current["action"] == "revoke" else "confirmed",
                "history": [{
                    "version": row["version"],
                    "action": row["action"],
                    "actor": row["actor"],
                    "reason": row["reason"],
                    "created_at": row["created_at"],
                    "snapshot": snapshots[(matter_id, row["version"])],
                } for row in rows],
            }
        return result
