"""Set-oriented metadata persistence; no Ledger or Fact update operations."""
from datetime import timedelta
from sqlalchemy import and_, or_, select, func, update

from backend.entity import (LedgerAccountParty, LedgerAccount, LedgerAccountRef,
                            LedgerEntry, ReviewAllocation, TransactionFact, TransactionImportRow)
from backend.entity.base import utc_now
from backend.mapper.review_command_mapper import ReviewCommandMapper, chunks
from backend.mapper.bounded_query_mapper import page_rows, scan_rows


class AccountManagementMapper(ReviewCommandMapper):
    entities = {"party": LedgerAccountParty, "account": LedgerAccount, "ref": LedgerAccountRef}

    def get(self, kind, entity_id):
        return self.db.execute(select(self.entities[kind].__table__).where(
            self.entities[kind].id == entity_id)).mappings().one_or_none()

    def public_statement(self, kind):
        """Light current ownership names in one bounded query; no raw evidence."""
        entity = self.entities[kind]
        statement = select(entity.__table__)
        if kind == "account":
            return statement.add_columns(func.coalesce(LedgerAccountParty.name, "").label("party_name")).outerjoin(
                LedgerAccountParty, LedgerAccountParty.id == LedgerAccount.party_id)
        if kind == "ref":
            return statement.add_columns(func.coalesce(LedgerAccount.name, "").label("account_name"),
                func.coalesce(LedgerAccount.party_id, 0).label("party_id"),
                func.coalesce(LedgerAccountParty.name, "").label("party_name")).outerjoin(
                LedgerAccount, LedgerAccount.id == LedgerAccountRef.account_id).outerjoin(
                LedgerAccountParty, LedgerAccountParty.id == LedgerAccount.party_id)
        return statement

    def public_get(self, kind, entity_id):
        return self.db.execute(self.public_statement(kind).where(
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
        return page_rows(self.db, self.public_statement(kind), request,
            self.query_columns(kind))

    def search(self, kind, request, project):
        return scan_rows(self.db, self.public_statement(kind), request,
            self.query_columns(kind),
            scope=f"local:ledger-v1:metadata:{kind}", project=project)

    def query_columns(self, kind):
        columns = {column.name: column for column in self.entities[kind].__table__.columns}
        if kind == "ref":
            # Match the public current-ownership projection, including the
            # explicit zero of unassigned refs for the != comparison.
            columns["party_id"] = func.coalesce(LedgerAccount.party_id, 0)
        return columns

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
                .where(LedgerEntry.account_ref_id.in_(batch), TransactionImportRow.row_status == 1)
                .group_by(LedgerEntry.account_ref_id))
            result.update(dict(rows.all()))
        return result

    def ref_scope(self, dimension, entity_id):
        """Resolve the current ref set as an indexed subquery, never a giant IN list."""
        statement = select(LedgerAccountRef.id)
        if dimension == "account_ref_id":
            return statement.where(LedgerAccountRef.id == entity_id)
        if dimension == "account_id":
            return statement.where(LedgerAccountRef.account_id == entity_id)
        if dimension == "party_id":
            return statement.join(LedgerAccount, LedgerAccount.id == LedgerAccountRef.account_id).where(
                LedgerAccount.party_id == entity_id)
        raise ValueError("unknown account scope")

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
