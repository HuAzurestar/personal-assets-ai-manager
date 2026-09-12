from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, time, timedelta

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.database import (
    Account,
    AccountBinding,
    AccountRevision,
    Bill,
    ImportArtifact,
    ImportBatch,
    ImportEvidence,
    ImportIdentity,
    ImportPreview,
    LedgerOrigin,
    RefundDesignation,
    RefundNatureAudit,
)
from app.money import money
from app.review_matters import allocated_bills
from app.smart_import import account_dict, build_plan, dump
from app.statement_parser import BANKS


class IntakeMapper:
    """All persistence used by the multi-source intake use case."""

    def __init__(self, db: Session):
        self.db = db

    def plan(
        self,
        documents: list[dict[str, object]],
        accounts: dict[str, str] | None = None,
        decisions: dict[str, str] | None = None,
    ) -> dict[str, object]:
        account_rows = self.db.execute(select(
            Account.id,
            Account.identity,
            Account.provider,
            Account.display_name,
            Account.number,
            Account.owner,
        )).mappings().all()
        known_accounts = {
            row["id"]: {key: row[key] for key in [
                "id", "identity", "provider", "display_name", "number", "owner"
            ]}
            for row in account_rows
        }
        bindings = {
            row["detected_identity"]: row["account_id"]
            for row in self.db.execute(select(
                AccountBinding.detected_identity,
                AccountBinding.account_id,
            )).mappings().all()
        }
        return build_plan(
            documents,
            known_accounts,
            bindings,
            self._planning_history,
            accounts,
            decisions,
        )

    def _planning_history(
        self,
        lookup_keys: set[str],
        occurred_values: list[datetime],
        source_types: set[str],
        references: set[str],
        upload_hashes: set[str],
    ) -> dict[str, object]:
        identities = {
            row["key"]: row["bill_id"]
            for row in self.db.execute(select(
                ImportIdentity.key,
                ImportIdentity.bill_id,
            ).where(ImportIdentity.key.in_(lookup_keys))).mappings().all()
        }
        identity_bill_ids = set(identities.values())
        bill_filters = []
        if occurred_values:
            first_day = datetime.combine(min(occurred_values).date(), time.min)
            last_day = datetime.combine(
                max(occurred_values).date() + timedelta(days=1), time.min
            )
            bill_filters.append(
                (Bill.occurred_at >= first_day) & (Bill.occurred_at < last_day)
            )
        if identity_bill_ids:
            bill_filters.append(Bill.id.in_(identity_bill_ids))
        bill_query = select(Bill.id, Bill.amount, Bill.currency, Bill.occurred_at)
        bill_query = bill_query.where(or_(*bill_filters)) if bill_filters else bill_query.where(False)
        bill_rows = self.db.execute(bill_query).mappings().all()
        bills = {row["id"]: row for row in bill_rows}
        evidence: dict[int, list[dict[str, object]]] = defaultdict(list)
        evidence_rows = self.db.execute(select(
            ImportEvidence.bill_id,
            ImportEvidence.record_json,
        ).where(ImportEvidence.bill_id.in_(bills))).mappings().all()
        for item in evidence_rows:
            if item["bill_id"]:
                evidence[item["bill_id"]].append(json.loads(item["record_json"]))

        matched_references: dict[tuple[str, str], list[int]] = defaultdict(list)
        origin_query = select(
            LedgerOrigin.bill_id,
            LedgerOrigin.source_type,
            LedgerOrigin.source_reference,
            LedgerOrigin.raw_payload,
        )
        if source_types and references:
            origin_query = origin_query.where(
                LedgerOrigin.source_type.in_(source_types),
                LedgerOrigin.source_reference.in_(references),
            )
        else:
            origin_query = origin_query.where(False)
        for origin in self.db.execute(origin_query).mappings().all():
            raw = json.loads(origin["raw_payload"] or "{}")
            reference = next((
                raw[key].strip()
                for key in ["交易订单号", "交易单号", "支付宝交易号", "交易号"]
                if raw.get(key)
            ), origin["source_reference"])
            if reference and (
                origin["bill_id"] not in evidence
                or any(not record.get("profile") for record in evidence[origin["bill_id"]])
            ):
                matched_references[(origin["source_type"], reference)].append(
                    origin["bill_id"]
                )
        seen_files = set(self.db.scalars(select(ImportArtifact.sha256).where(
            ImportArtifact.sha256.in_(upload_hashes)
        )).all())
        return {
            "identities": identities,
            "bills": bills,
            "evidence": dict(evidence),
            "references": dict(matched_references),
            "seen_files": seen_files,
        }

    def create_preview(
        self,
        token: str,
        documents: list[dict[str, object]],
        plan: dict[str, object],
    ) -> None:
        cutoff = datetime.now() - timedelta(hours=24)
        expired_tokens = self.db.scalars(select(ImportPreview.token).where(
            ImportPreview.created_time < cutoff,
            ImportPreview.result_json == "",
        )).all()
        if expired_tokens:
            self.db.query(ImportPreview).filter(
                ImportPreview.token.in_(expired_tokens)
            ).delete(synchronize_session=False)
        self.db.add(ImportPreview(
            token=token,
            payload_json=dump(documents),
            plan_json=dump(plan),
        ))

    def preview(self, token: str) -> ImportPreview | None:
        return self.db.scalar(select(ImportPreview).where(ImportPreview.token == token))

    def revise_preview(
        self,
        stage: ImportPreview,
        documents: list[dict[str, object]],
        accounts: dict[str, str],
        decisions: dict[str, str],
        plan: dict[str, object],
    ) -> None:
        stage.payload_json = dump({
            "documents": documents,
            "accounts": accounts,
            "decisions": decisions,
        })
        stage.plan_json = dump(plan)

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def commit_plan(self, plan: dict[str, object]) -> dict[str, object]:
        documents = [doc for doc in plan["documents"] if not doc.get("duplicate")]
        rows = [(doc, row) for doc in documents for row in doc["rows"]]
        now = datetime.now()

        account_specs = {
            row["account"]["identity"]: row["account"]
            for _doc, row in rows
            if row.get("account") and row.get("match") is not None
        }
        account_cache = {
            account.identity: account
            for account in self.db.scalars(select(Account).where(
                Account.identity.in_(account_specs)
            )).all()
        }
        new_accounts = [
            Account(**{key: spec[key] for key in [
                "identity", "provider", "display_name", "number", "owner"
            ]})
            for identity, spec in account_specs.items()
            if identity not in account_cache
        ]
        self.db.add_all(new_accounts)
        self.db.flush()
        account_cache.update({account.identity: account for account in new_accounts})

        new_targets: dict[str, int] = {}
        new_bills: list[tuple[dict[str, object], Bill]] = []
        for _doc, row in rows:
            if row["action"] != "new":
                continue
            account = account_cache[row["account"]["identity"]]
            bill = Bill(
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                merchant=row["merchant"],
                note=row["note"],
                amount=money(row["amount_minor"]),
                currency=row["currency"],
                account_name=account.display_name,
                account_id=account.id,
                time_precision=row["time_precision"],
                import_nature=row["nature"],
                category="未分类",
                tags="",
                aggregate_excluded=row["nature"] == "neutral",
            )
            self.db.add(bill)
            new_bills.append((row, bill))
        self.db.flush()
        for row, bill in new_bills:
            new_targets[row["match"]] = bill.id

        batches: list[tuple[dict[str, object], ImportBatch]] = []
        for doc in documents:
            batch = ImportBatch(
                source_type=doc["source_type"],
                filename=doc["filename"],
                imported_at=now,
                row_count=len(doc["rows"]),
                imported_count=sum(row["action"] == "new" for row in doc["rows"]),
            )
            self.db.add(batch)
            batches.append((doc, batch))
        self.db.flush()
        self.db.add_all([ImportArtifact(
            import_batch_id=batch.id,
            source_type=doc["source_type"],
            filename=doc["filename"],
            file_format=doc["format"],
            archive_entry=doc["archive_entry"],
            sha256=doc["sha256"],
        ) for doc, batch in batches])

        resolved_rows = []
        bill_ids = set()
        for doc, batch in batches:
            for row in doc["rows"]:
                target = row.get("match")
                bill_id = new_targets.get(target, target) if target is not None else None
                resolved_rows.append((doc, batch, row, bill_id))
                if isinstance(bill_id, int):
                    bill_ids.add(bill_id)

        bills = {
            bill.id: bill
            for bill in self.db.scalars(select(Bill).where(Bill.id.in_(bill_ids))).all()
        }
        account_ids = {bill.account_id for bill in bills.values() if bill.account_id}
        account_by_id = {
            account.id: account
            for account in self.db.scalars(select(Account).where(Account.id.in_(account_ids))).all()
        }
        detected = {
            row["detected_account_identity"]
            for _doc, _batch, row, bill_id in resolved_rows
            if bill_id
            and row.get("detected_account_identity")
            and row["detected_account_identity"] != row["account"]["identity"]
        }
        bindings = {
            binding.detected_identity: binding
            for binding in self.db.scalars(select(AccountBinding).where(
                AccountBinding.detected_identity.in_(detected)
            )).all()
        }
        revision_bill_ids = set(self.db.scalars(select(AccountRevision.bill_id).where(
            AccountRevision.bill_id.in_(bill_ids)
        )).all())
        origin_bill_ids = set(self.db.scalars(select(LedgerOrigin.bill_id).where(
            LedgerOrigin.bill_id.in_(bill_ids)
        )).all())
        platform_evidence_bill_ids = set(self.db.scalars(select(ImportEvidence.bill_id).join(
            ImportBatch,
            ImportEvidence.import_batch_id == ImportBatch.id,
        ).where(
            ImportEvidence.bill_id.in_(bill_ids),
            ImportBatch.source_type.not_in(BANKS),
        )).all())
        refund_bill_ids = set(self.db.scalars(select(RefundDesignation.bill_id).where(
            RefundDesignation.bill_id.in_(bill_ids)
        )).all())
        refund_bill_ids.update(bill.id for row, bill in new_bills if row["nature"] == "refund")
        identity_keys = {
            key for _doc, _batch, row, bill_id in resolved_rows if bill_id for key in row["keys"]
        }
        existing_identity_keys = set(self.db.scalars(select(ImportIdentity.key).where(
            ImportIdentity.key.in_(identity_keys)
        )).all())
        protected_bill_ids = set(allocated_bills(self.db))
        protected_bill_ids.update(self.db.scalars(select(RefundNatureAudit.bill_id).where(
            RefundNatureAudit.bill_id.in_(bill_ids)
        )).all())

        for row, bill in new_bills:
            if row["nature"] == "refund":
                self.db.add(RefundDesignation(bill_id=bill.id, created_at=now))

        for doc, batch, row, bill_id in resolved_rows:
            if isinstance(bill_id, int):
                detected_identity = row.get("detected_account_identity")
                if detected_identity and detected_identity != row["account"]["identity"]:
                    selected = account_cache[row["account"]["identity"]]
                    binding = bindings.get(detected_identity)
                    if binding is None:
                        binding = AccountBinding(
                            detected_identity=detected_identity,
                            account_id=selected.id,
                            basis=row.get("account_basis", "导入确认"),
                        )
                        self.db.add(binding)
                        bindings[detected_identity] = binding
                    else:
                        binding.account_id = selected.id
                        binding.basis = row.get("account_basis", "导入确认")

                bill = bills[bill_id]
                previous_account = account_by_id.get(bill.account_id)
                if (
                    row["source_type"] in BANKS
                    and previous_account
                    and previous_account.number.startswith("****")
                    and row["account"]["number"].endswith(previous_account.number[-4:])
                    and bill_id not in revision_bill_ids
                ):
                    account = account_cache[row["account"]["identity"]]
                    bill.account_id = account.id
                    bill.account_name = account.display_name
                if bill_id not in origin_bill_ids:
                    self.db.add(LedgerOrigin(
                        bill_id=bill_id,
                        source_type=doc["source_type"],
                        source_reference=row["reference"],
                        import_batch_id=batch.id,
                        source_row_number=row["row_number"],
                        raw_payload=dump(row["raw"]),
                    ))
                    origin_bill_ids.add(bill_id)
                if not bill.note and row["note"]:
                    bill.note = row["note"]
                if bill.time_precision == "day" and row["time_precision"] == "second":
                    bill.occurred_at = datetime.fromisoformat(row["occurred_at"])
                    bill.time_precision = "second"
                semantic_editable = (
                    bill.id not in protected_bill_ids
                    and not bill.duplicate_of_id
                    and not bill.transfer_group_id
                )
                if (
                    semantic_editable
                    and row["source_type"] not in BANKS
                    and row["account"]["provider"] in BANKS
                ):
                    was_auto_neutral = bill.import_nature == "neutral"
                    if bill.import_nature != "refund":
                        bill.import_nature = row["nature"]
                    if bill.import_nature == "neutral":
                        bill.aggregate_excluded = True
                    elif was_auto_neutral:
                        bill.aggregate_excluded = False
                    platform_evidence_bill_ids.add(bill_id)
                elif (
                    semantic_editable
                    and row["source_type"] in BANKS
                    and row["nature"] in {"neutral", "refund"}
                    and bill_id not in platform_evidence_bill_ids
                ):
                    if bill.import_nature != "refund":
                        bill.import_nature = row["nature"]
                    bill.aggregate_excluded = bill.import_nature == "neutral"
                if semantic_editable and bill.import_nature == "refund" and bill_id not in refund_bill_ids:
                    self.db.add(RefundDesignation(bill_id=bill_id, created_at=now))
                    refund_bill_ids.add(bill_id)
                for key in row["keys"]:
                    if key not in existing_identity_keys:
                        self.db.add(ImportIdentity(key=key, bill_id=bill_id))
                        existing_identity_keys.add(key)

            self.db.add(ImportEvidence(
                bill_id=bill_id,
                import_batch_id=batch.id,
                row_number=row["row_number"],
                record_json=dump(row),
                disposition=row["action"] if bill_id else row["disposition"],
            ))
        self.db.flush()
        return {"counts": plan["counts"], "batch_ids": [batch.id for _doc, batch in batches]}

    def history(self, limit: int = 30) -> list[dict[str, object]]:
        rows = self.db.execute(select(
            ImportBatch.id,
            ImportBatch.filename,
            ImportBatch.source_type,
            ImportBatch.row_count,
            ImportBatch.imported_count,
            ImportBatch.imported_at,
        ).where(
            ImportBatch.id.in_(select(ImportEvidence.import_batch_id))
        ).order_by(ImportBatch.id.desc()).limit(limit)).mappings().all()
        return [{
            "id": row["id"],
            "filename": row["filename"],
            "source_type": row["source_type"],
            "row_count": row["row_count"],
            "imported_count": row["imported_count"],
            "imported_at": row["imported_at"].isoformat(),
        } for row in rows]

    def accounts(self) -> list[dict[str, object]]:
        rows = self.db.scalars(select(Account).order_by(Account.display_name)).all()
        return [account_dict(account) for account in rows]

    def rows(self, batch_id: int) -> list[dict[str, object]]:
        rows = self.db.execute(select(
            ImportEvidence.id,
            ImportEvidence.bill_id,
            ImportEvidence.disposition,
            ImportEvidence.record_json,
        ).where(
            ImportEvidence.import_batch_id == batch_id
        ).order_by(ImportEvidence.row_number)).mappings().all()
        return [{
            "id": row["id"],
            "bill_id": row["bill_id"],
            "disposition": row["disposition"],
            "record": json.loads(row["record_json"]),
        } for row in rows]

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
