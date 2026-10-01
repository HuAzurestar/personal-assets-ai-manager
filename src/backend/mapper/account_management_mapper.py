"""Set-oriented metadata persistence; no Ledger or Fact update operations."""
from datetime import timedelta
from sqlalchemy import and_, or_, not_, select, func, update

from backend.entity import (LedgerAccountParty, LedgerAccount, LedgerAccountRef,
                            LedgerEntry, ReviewAllocation, TransactionFact, TransactionImportRow)
from backend.entity.base import utc_now
from backend.mapper.review_command_mapper import ReviewCommandMapper, chunks
from backend.schema.list_query import FilterFieldExpression


class AccountManagementMapper(ReviewCommandMapper):
    entities = {"party": LedgerAccountParty, "account": LedgerAccount, "ref": LedgerAccountRef}

    def get(self, kind, entity_id):
        return self.db.execute(select(self.entities[kind].__table__).where(
            self.entities[kind].id == entity_id)).mappings().one_or_none()

    def create(self, kind, values):
        now = utc_now()
        row = self.entities[kind](**values, status="ACTIVE", created_time=now, updated_time=now)
        self.db.add(row)
        self.db.flush()
        return dict(self.get(kind, row.id))

    def edit(self, kind, row, values):
        values = values | {"updated_time": max(utc_now(), row["updated_time"] + timedelta(microseconds=1))}
        self.db.execute(update(self.entities[kind]).where(self.entities[kind].id == row["id"]).values(**values))
        return dict(self.get(kind, row["id"]))

    def page(self, kind, request):
        entity = self.entities[kind]
        def predicate(expr):
            if expr is None:
                return True
            if isinstance(expr, FilterFieldExpression):
                column = getattr(entity, expr.key)
                return column == expr.val if expr.op == "=" else column != expr.val
            children = [predicate(child) for child in expr.expression]
            return {"AND": and_, "OR": or_, "NOT": lambda x: not_(x)}[expr.op](*children)
        condition = predicate(request.filter)
        sorts = [(item.key, item.direction) for item in request.sorter] or [("id", "asc")]
        if not any(key == "id" for key, _ in sorts):
            sorts.append(("id", "asc"))
        order = [getattr(getattr(entity, key), direction)() for key, direction in sorts]
        total = self.db.scalar(select(func.count()).select_from(entity).where(condition))
        rows = self.db.execute(select(entity.__table__).where(condition).order_by(*order).offset(
            (request.page_index - 1) * request.page_size).limit(request.page_size)).mappings()
        return [dict(row) for row in rows], total

    def ledger_effect(self, ref_id):
        return self.db.execute(select(func.count(LedgerEntry.id).label("count"),
                                      func.max(LedgerEntry.id).label("last_id"),
                                      func.max(LedgerEntry.updated_time).label("updated_time"))
                               .where(LedgerEntry.account_ref_id == ref_id)).mappings().one()

    def source_times(self, ref_ids):
        result = {}
        for batch in chunks(ref_ids):
            rows = self.db.execute(select(LedgerEntry.account_ref_id, func.max(TransactionImportRow.created_time))
                .join(ReviewAllocation, ReviewAllocation.ledger_entry_id == LedgerEntry.id)
                .join(TransactionFact, TransactionFact.id == ReviewAllocation.transaction_fact_id)
                .join(TransactionImportRow, TransactionImportRow.transaction_fact_id == TransactionFact.id)
                .where(LedgerEntry.account_ref_id.in_(batch)).group_by(LedgerEntry.account_ref_id))
            result.update(dict(rows.all()))
        return result

    def reliable_refs(self, identities):
        """Exact namespace identity pairs only; no inferred fuzzy match."""
        result = {}
        identities = sorted(set(identities))
        for offset in range(0, len(identities), 200):
            conditions = [and_(LedgerAccountRef.source_namespace == namespace,
                               LedgerAccountRef.source_identity == identity)
                          for namespace, identity in identities[offset:offset + 200]]
            for row in self.db.execute(select(LedgerAccountRef.__table__).where(
                LedgerAccountRef.identity_strength == 1, or_(*conditions))).mappings():
                result[(row["source_namespace"], row["source_identity"])] = dict(row)
        return result

    def create_reliable_refs(self, identities):
        existing = self.reliable_refs(identities)
        now = utc_now()
        new = [LedgerAccountRef(source_namespace=namespace, source_identity=identity,
               identity_strength=1, account_id=0, name="", institution="", reference="", status="ACTIVE",
               created_time=now, updated_time=now)
               for namespace, identity in sorted(set(identities) - set(existing))]
        self.db.add_all(new)
        self.db.flush()
        created = self.named_rows("refs", [row.id for row in new])
        existing.update({(row["source_namespace"], row["source_identity"]): row for row in created})
        return existing
