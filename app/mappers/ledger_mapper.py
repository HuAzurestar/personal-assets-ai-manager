from __future__ import annotations

import json
from datetime import datetime, time

from sqlalchemy import asc, desc, func, select, union_all
from sqlalchemy.orm import Session

from app.core.errors import MultipleTagsForView, UnknownTagSelector
from app.database import (
    Bill,
    ImportBatch,
    ImportEvidence,
    LedgerOrigin,
    TagAudit,
    TagView,
    ViewTag,
)
from app.schemas.ledger import LedgerBillVO, LedgerPageQuery, LedgerPageVO, LedgerTagVO


class LedgerMapper:
    """Own all SQL required by the ledger list read model.

    The mapper deliberately selects explicit columns and performs a fixed number
    of set-based queries. It never executes SQL while assembling individual rows.
    """

    def __init__(self, db: Session):
        self.db = db

    def page(self, query: LedgerPageQuery) -> LedgerPageVO:
        views, tags_by_key, tags_by_id = self.tag_dictionary()
        clauses = self._clauses(query, views, tags_by_key, tags_by_id)
        order_column = Bill.occurred_at if query.sort_by == "occurred_at" else Bill.amount
        order_fn = asc if query.sort_order == "asc" else desc

        total = self.db.scalar(select(func.count(Bill.id)).where(*clauses)) or 0
        page_ids = self.db.scalars(
            select(Bill.id)
            .where(*clauses)
            .order_by(order_fn(order_column), order_fn(Bill.id))
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        ).all()
        items_by_id = self.by_ids(
            page_ids,
            tag_dictionary=(views, tags_by_key, tags_by_id),
        )

        items = tuple(items_by_id[bill_id] for bill_id in page_ids if bill_id in items_by_id)
        return LedgerPageVO(
            items=items,
            total=total,
            page=query.page,
            page_size=query.page_size,
            filters={
                "date_from": str(query.date_from) if query.date_from else None,
                "date_to": str(query.date_to) if query.date_to else None,
                "amount_min": query.amount_min,
                "amount_max": query.amount_max,
                "source": list(query.source),
                "account": list(query.account),
                "direction": query.direction,
                "q": query.q,
                "tag": list(query.tag),
            },
            sort={"by": query.sort_by, "order": query.sort_order},
        )

    def all(self) -> tuple[LedgerBillVO, ...]:
        """Compatibility read for legacy callers; new UI code must use page()."""
        bill_ids = self.db.scalars(
            select(Bill.id).order_by(Bill.occurred_at.desc(), Bill.id.desc())
        ).all()
        bills = self.by_ids(bill_ids)
        return tuple(bills[bill_id] for bill_id in bill_ids if bill_id in bills)

    def by_ids(
        self,
        bill_ids: list[int],
        *,
        tag_dictionary=None,
    ) -> dict[int, LedgerBillVO]:
        """Load complete list-card VOs in bounded set queries.

        Callers may pass a previously loaded tag dictionary so a page query does
        not fetch the same small lookup tables twice.
        """
        unique_ids = list(dict.fromkeys(bill_ids))
        if not unique_ids:
            return {}
        views, tags_by_key, _ = tag_dictionary or self.tag_dictionary()
        statement = (
            select(
                Bill.id,
                Bill.occurred_at,
                Bill.merchant,
                Bill.note,
                Bill.amount,
                Bill.currency,
                Bill.category,
                Bill.account_name,
                Bill.aggregate_excluded,
                Bill.transfer_group_id,
                Bill.duplicate_of_id,
                Bill.tag_state_json,
            )
            .where(Bill.id.in_(unique_ids))
        )
        rows_by_id = {
            row["id"]: row
            for row in self.db.execute(statement).mappings().all()
        }
        origins = self._origins(unique_ids)
        revision_ids = self._tag_revision_ids(unique_ids)
        return {
            bill_id: self._bill_vo(rows_by_id[bill_id], views, tags_by_key, origins, revision_ids)
            for bill_id in unique_ids
            if bill_id in rows_by_id
        }

    def filter_clauses(self, query: LedgerPageQuery, *, tag_dictionary=None) -> list:
        """Build the shared ledger filter with at most one tag dictionary load."""
        if tag_dictionary is None:
            tag_dictionary = self.tag_dictionary() if query.tag else ((), {}, {})
        views, tags_by_key, tags_by_id = tag_dictionary
        return self._clauses(query, views, tags_by_key, tags_by_id)

    def tag_dictionary(self):
        view_rows = self.db.execute(
            select(TagView.id, TagView.name, TagView.system_name)
            .where(TagView.archived.is_(False))
            .order_by(TagView.id)
        ).mappings().all()
        view_ids = [row["id"] for row in view_rows]
        tag_rows = self.db.execute(
            select(
                ViewTag.id,
                ViewTag.view_id,
                ViewTag.name,
                ViewTag.system_name,
                ViewTag.is_unclassified,
            )
            .where(ViewTag.view_id.in_(view_ids), ViewTag.archived.is_(False))
            .order_by(ViewTag.id)
        ).mappings().all() if view_ids else []
        by_key = {(row["view_id"], row["system_name"]): row for row in tag_rows}
        by_id = {row["id"]: row for row in tag_rows}
        return view_rows, by_key, by_id

    def _clauses(self, query, views, tags_by_key, tags_by_id):
        excluded = {"effective": False, "all": None, "excluded": True}[query.scope]
        clauses = [] if excluded is None else [Bill.aggregate_excluded.is_(excluded)]
        if query.date_from:
            clauses.append(Bill.occurred_at >= datetime.combine(query.date_from, time.min))
        if query.date_to:
            clauses.append(Bill.occurred_at <= datetime.combine(query.date_to, time.max))
        if query.amount_min is not None:
            clauses.append(func.abs(Bill.amount) >= query.amount_min)
        if query.amount_max is not None:
            clauses.append(func.abs(Bill.amount) <= query.amount_max)
        if query.source:
            clauses.append(Bill.id.in_(
                union_all(
                    select(LedgerOrigin.bill_id).where(
                        LedgerOrigin.source_type.in_(query.source)
                    ),
                    select(ImportEvidence.bill_id).join(
                        ImportBatch,
                        ImportBatch.id == ImportEvidence.import_batch_id,
                    ).where(
                        ImportEvidence.bill_id.is_not(None),
                        ImportBatch.source_type.in_(query.source),
                    ),
                )
            ))
        if query.account:
            clauses.append(Bill.account_name.in_(query.account))
        if query.direction == "income":
            clauses.append(Bill.amount > 0)
        elif query.direction == "expense":
            clauses.append(Bill.amount < 0)
        elif query.direction == "transfer":
            clauses.append(Bill.transfer_group_id.is_not(None))
        if query.q:
            needle = f"%{query.q.strip()}%"
            clauses.append(Bill.merchant.ilike(needle) | Bill.note.ilike(needle))

        views_by_id = {row["id"]: row for row in views}
        views_by_name = {row["system_name"]: row for row in views}
        selected_by_view: dict[str, str] = {}
        for selector in query.tag:
            try:
                view_selector, tag_selector = selector.split(":", 1)
            except ValueError as error:
                raise UnknownTagSelector("tag must use view_system_name:tag_system_name") from error
            if view_selector.isdigit() and tag_selector.isdigit():
                view = views_by_id.get(int(view_selector))
                tag = tags_by_id.get(int(tag_selector))
                if not view or not tag or tag["view_id"] != view["id"]:
                    raise UnknownTagSelector("tag does not belong to the requested view")
                view_system_name = view["system_name"]
                tag_system_name = tag["system_name"]
            else:
                view = views_by_name.get(view_selector)
                tag = tags_by_key.get((view["id"], tag_selector)) if view else None
                if not view or not tag:
                    raise UnknownTagSelector("tag does not belong to the requested view")
                view_system_name = view_selector
                tag_system_name = tag_selector
            if view_system_name in selected_by_view and selected_by_view[view_system_name] != tag_system_name:
                raise MultipleTagsForView("only one tag may be selected in each view")
            selected_by_view[view_system_name] = tag_system_name
        for view_system_name, tag_system_name in selected_by_view.items():
            clauses.append(func.coalesce(
                func.json_extract(Bill.tag_state_json, f"$.{view_system_name}"),
                "unclassified",
            ) == tag_system_name)
        return clauses

    def _origins(self, bill_ids: list[int]) -> dict[int, dict]:
        if not bill_ids:
            return {}
        rows = self.db.execute(
            select(
                LedgerOrigin.bill_id,
                LedgerOrigin.source_type,
                LedgerOrigin.source_reference,
                LedgerOrigin.import_batch_id,
            ).where(LedgerOrigin.bill_id.in_(bill_ids))
        ).mappings().all()
        return {row["bill_id"]: row for row in rows}

    def _tag_revision_ids(self, bill_ids: list[int]) -> dict[int, int]:
        if not bill_ids:
            return {}
        rows = self.db.execute(
            select(TagAudit.bill_id, func.max(TagAudit.id).label("revision_id"))
            .where(TagAudit.bill_id.in_(bill_ids), TagAudit.superseded.is_(False))
            .group_by(TagAudit.bill_id)
        ).mappings().all()
        return {row["bill_id"]: row["revision_id"] for row in rows}

    @staticmethod
    def _bill_vo(row, views, tags_by_key, origins, revision_ids) -> LedgerBillVO:
        try:
            state = json.loads(row["tag_state_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        assignments = []
        for view in views:
            tag = tags_by_key.get((view["id"], state.get(view["system_name"], "unclassified")))
            if not tag:
                tag = tags_by_key.get((view["id"], "unclassified"))
            if tag:
                assignments.append(LedgerTagVO(
                    view_id=view["id"],
                    view_name=view["name"],
                    view_system_name=view["system_name"],
                    tag_id=tag["id"],
                    tag_name=tag["name"],
                    tag_system_name=tag["system_name"],
                ))
        origin = origins.get(row["id"])
        return LedgerBillVO(
            id=row["id"],
            occurred_at=row["occurred_at"],
            merchant=row["merchant"],
            note=row["note"],
            amount=row["amount"],
            currency=row["currency"],
            category=row["category"],
            account_name=row["account_name"],
            aggregate_excluded=row["aggregate_excluded"],
            transfer_group_id=row["transfer_group_id"],
            duplicate_of_id=row["duplicate_of_id"],
            tag_state=state,
            source_type=origin["source_type"] if origin else None,
            source_reference=origin["source_reference"] if origin else None,
            import_batch_id=origin["import_batch_id"] if origin else None,
            tag_revision_id=revision_ids.get(row["id"], 0),
            tags=tuple(assignments),
        )
