"""One-snapshot cash/evidence reads, distinct from immutable publication."""
from backend.core.import_public_text import masked_reference, masked_summary
from backend.entity import Position, PositionLeg, ReviewCase, TransactionFact, ReviewAllocation
from backend.error import TargetEconomicError
from backend.mapper.bounded_query_mapper import query_budget, canonical
from backend.mapper.flow_read_mapper import FlowReadMapper
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.schema.flow_read import LedgerEntryListBody, LedgerEntryDetailRead, FlowAccountOwnership, FlowListItem
from backend.service.account_management_service import AccountManagementService
from backend.service.review_command_service import flow_po, review_po


def limited(value):
    encoded = canonical(value.model_dump() if hasattr(value, "model_dump") else value).encode()
    if len(encoded) > 2 * 1024 * 1024:
        raise TargetEconomicError(413, "read response exceeds budget; use paged relations or smaller page", code="DETAIL_LIMIT")
    return value


class FlowReadService:
    def __init__(self, db):
        self.mapper = FlowReadMapper(db)
        self.relations = TrustedRelationMapper(db)

    def _snapshot(self):
        self.relations.read_snapshot()
        self.relations.validate()

    def _get(self, ledger_id):
        if type(ledger_id) is not int or not 1 <= ledger_id <= 2**63 - 1:
            raise TargetEconomicError(422, "invalid Flow identity", code="LIST_FILTER_VALUE_INVALID")
        row = self.mapper.get(ledger_id)
        if row is None:
            raise TargetEconomicError(404, "Flow not found", code="FLOW_NOT_FOUND")
        return row

    def page(self, request):
        with query_budget(self.mapper.db):
            self._snapshot()
            rows, total = self.mapper.page(request)
            return limited(LedgerEntryListBody(items=self._items(rows), total=total,
                page_index=request.page_index, page_size=request.page_size))

    def _items(self, rows):
        refs = self.mapper.account_rows([row["account_ref_id"] for row in rows])
        manager = AccountManagementService(self.mapper.db)
        accounts = {0: dict(state="UNIDENTIFIED", display_label="来源未识别")}
        for ref_id, ref in refs.items():
            accounts[ref_id] = dict(state="ASSIGNED" if ref["account_id"] else "UNASSIGNED",
                display_label=manager._po("ref", ref)["display_label"])
        result = []
        for row in rows:
            if row["account_ref_id"] not in accounts:
                raise TargetEconomicError(409, "account ownership chain is broken", code="ACCOUNT_RELATION_BROKEN")
            result.append(FlowListItem(**flow_po(row), active=row["review_status"] == 0,
                summary=masked_summary(row["summary"]), transaction_id=row["transaction_id"],
                review=dict(id=row["review_id"], title=masked_summary(row["review_title"]),
                    status="CONFIRMED" if row["review_status"] == 0 else "REVOKED"),
                account=accounts[row["account_ref_id"]]))
        return result

    @staticmethod
    def _search_projection(row):
        return row | dict(summary=masked_summary(row["summary"]), counterparty=masked_summary(row["counterparty"]))

    def search(self, request):
        with query_budget(self.mapper.db):
            self._snapshot()
            result = self.mapper.search(request, self._search_projection)
            result["items"] = [item.model_dump() for item in self._items(result["items"])]
            return limited(result)

    def _account(self, ref_id):
        ref, account, party = self.mapper.ownership(ref_id)
        if ref is None:
            return FlowAccountOwnership(state="UNIDENTIFIED", ref=None, account=None, party=None)
        # Reuse the existing masked Account projection, not a second identity
        # resolver. This is still the same explicitly started read snapshot.
        manager = AccountManagementService(self.mapper.db)
        times = manager.mapper.source_times([ref_id])
        return FlowAccountOwnership(state="ASSIGNED" if account else "UNASSIGNED",
            ref=manager._po("ref", ref, times), account=manager._po("account", account) if account else None,
            party=manager._po("party", party) if party else None)

    def _tags(self, ledger, rows):
        if any(row["tag_id"] is None or row["view_id"] is None for row in rows):
            raise TargetEconomicError(409, "tag relation is damaged", code="TAG_RELATION_BROKEN")
        sources = self.mapper.approved_sources(ledger["id"], [row["view_id"] for row in rows])
        result = []
        for row in rows:
            source = sources.get(row["view_id"])
            known = (ledger["review_status"] == 0 and ledger["entry_type"] != 3
                and row["tag_status"] == row["view_status"] == "ACTIVE"
                and row["tag_system_name"] != "unclassified" and source is not None
                and source["source_count"] == 1 and source["tag_id"] == row["tag_id"]
                and source["rule_revision"] == source["current_revision"]
                and source["rule_view_id"] == row["view_id"])
            result.append({key: value for key, value in row.items() if key != "ledger_id"} |
                dict(source_type="AUTO_RULE" if known else "UNKNOWN",
                    **{key: source[key] if known else None for key in ("request_id", "rule_id", "rule_revision")}))
        return result

    @staticmethod
    def _fact(fact):
        return dict(id=fact["id"], occurred_time=fact["occurred_time"],
            cash_direction="IN" if fact["cash_direction"] == 1 else "OUT", cash_amount=fact["amount"],
            cash_currency_code=fact["currency_code"], account_code=masked_reference(fact["account_code"]),
            counterparty_name=masked_summary(fact["counterparty_name"]), summary=masked_summary(fact["summary"]))

    def detail(self, ledger_id):
        with query_budget(self.mapper.db):
            self._snapshot()
            ledger = self._get(ledger_id)
            self.mapper.tag_guard(ledger_id)
            allocation, fact, review = self.mapper.originals(ledger_id)
            links, legs, positions = self.mapper.positions(ledger_id)
            tags = self.mapper.tag_rows(ledger_id)
            account = self._account(ledger["account_ref_id"])
            owner_rows = sum(value is not None for value in (account.ref, account.account, account.party))
            if 3 + owner_rows + len(links) + len(legs) + len(positions) + len(tags) > 4000:
                raise TargetEconomicError(413, "detail relation budget exceeded; use paged relations", code="DETAIL_LIMIT")
            units = {row["id"]: row["unit_code"] for row in positions}
            result = LedgerEntryDetailRead(ledger_entry=flow_po(ledger), active=ledger["review_status"] == 0,
                summary=masked_summary(fact["summary"]), tags=self._tags(ledger, tags),
                allocations=[allocation], facts=[self._fact(fact)],
                reviews=[review_po(review)], account=account,
                positions=positions, position_legs=[row | dict(unit_code=units[row["position_id"]]) for row in legs],
                position_allocations=links,
                position_identity_state="KNOWN" if legs else "NEEDS_IDENTITY" if ledger["entry_type"] == 2 else "NOT_APPLICABLE")
            return limited(result)

    def source_page(self, ledger_id, request):
        with query_budget(self.mapper.db):
            self._snapshot()
            ledger = self._get(ledger_id)
            rows, total = self.mapper.source_page(ledger_id, request)
            items = []
            account = self._account(ledger["account_ref_id"])
            for row in rows:
                fact = {column.name: row["fact__" + column.name] for column in TransactionFact.__table__.columns}
                review = {column.name: row["review__" + column.name] for column in ReviewCase.__table__.columns}
                allocation = {column.name: row[column.name] for column in ReviewAllocation.__table__.columns}
                items.append(dict(allocation=allocation, fact=self._fact(fact), review=review_po(review),
                    ledger_entry=flow_po(ledger), active=ledger["review_status"] == 0, account=account.model_dump()))
            return limited(dict(items=items, total=total, page_index=request.page_index, page_size=request.page_size))

    def position_page(self, ledger_id, request):
        with query_budget(self.mapper.db):
            self._snapshot()
            self._get(ledger_id)
            rows, total = self.mapper.position_page(ledger_id, request)
            items = []
            for row in rows:
                position = {column.name: row["position__" + column.name] for column in Position.__table__.columns}
                leg = {column.name: row["leg__" + column.name] for column in PositionLeg.__table__.columns}
                review = {column.name: row["review__" + column.name] for column in ReviewCase.__table__.columns}
                allocation = {key: row[key] for key in ("id", "review_id", "ledger_id", "position_leg_id", "cash_amount",
                    "cash_currency_code", "created_time", "updated_time")}
                items.append(dict(allocation=allocation, position_leg=leg | dict(unit_code=position["unit_code"]),
                    position=position, review=review_po(review)))
            return limited(dict(items=items, total=total, page_index=request.page_index, page_size=request.page_size))

    def tag_page(self, ledger_id, request):
        with query_budget(self.mapper.db):
            self._snapshot()
            ledger = self._get(ledger_id)
            self.mapper.tag_guard(ledger_id)
            rows, total = self.mapper.tag_page(ledger_id, request)
            return limited(dict(items=self._tags(ledger, rows), total=total,
                page_index=request.page_index, page_size=request.page_size))
