from __future__ import annotations

import json
from dataclasses import asdict

from sqlalchemy.orm import Session

from app.mappers.target_migration_mapper import TargetMigrationMapper
from app.schemas.migration import (
    LedgerEntryShadowVO,
    LedgerEntrySourceShadowVO,
    LedgerEntryTagShadowVO,
    LedgerProjectionShadowReport,
    ProjectionFactVO,
    ProjectionReviewCaseVO,
    ProjectionReviewLineVO,
)
from app.services.review_migration_service import ReviewMatterShadowMigrationService


FINANCIAL_REVIEW_TYPES = {
    "AA",
    "LOAN_BORROW",
    "LOAN_LEND",
    "REFUND",
    "TRANSFER",
    "FX_EXCHANGE",
    "DUPLICATE",
}
ENHANCEMENT_REVIEW_TYPES = {"ACCOUNT", "TAG", "FACT_CONFLICT"}


class LedgerProjectionShadowMigrationService:
    """Build deterministic hot entries from verified target facts and Review."""

    def __init__(self, db: Session):
        self.db = db
        self.mapper = TargetMigrationMapper(db)

    def backfill_and_compare(self) -> LedgerProjectionShadowReport:
        blockers: list[str] = []
        facts = self.mapper.projection_facts()
        cases = self.mapper.projection_confirmed_cases()
        lines = self.mapper.projection_review_lines([case.id for case in cases])
        tag_views = self.mapper.target_tag_views()
        tags = self.mapper.target_tags()
        expected_entries, expected_sources, expected_tags = self._expected(
            facts,
            cases,
            lines,
            tag_views,
            tags,
            blockers,
        )
        current_entries = self.mapper.target_ledger_entries()
        current_sources = self.mapper.target_ledger_entry_sources()
        current_tags = self.mapper.target_ledger_entry_tags()
        entry_inserts = self._missing(expected_entries, current_entries)
        source_inserts = self._missing(expected_sources, current_sources)
        tag_inserts = self._missing(expected_tags, current_tags)
        try:
            self.mapper.insert_ledger_entries([asdict(row) for row in entry_inserts])
            self.mapper.insert_ledger_entry_sources([asdict(row) for row in source_inserts])
            self.mapper.insert_ledger_entry_tags([asdict(row) for row in tag_inserts])
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        actual_entries = self.mapper.target_ledger_entries()
        actual_sources = self.mapper.target_ledger_entry_sources()
        actual_tags = self.mapper.target_ledger_entry_tags()
        support = ReviewMatterShadowMigrationService
        entry_report = support._table_report(
            expected_entries,
            actual_entries,
            len(entry_inserts),
        )
        source_report = support._table_report(
            expected_sources,
            actual_sources,
            len(source_inserts),
        )
        tag_report = support._table_report(
            expected_tags,
            actual_tags,
            len(tag_inserts),
        )
        return LedgerProjectionShadowReport(
            matched=(
                not blockers
                and not entry_report.mismatched_ids
                and not source_report.mismatched_ids
                and not tag_report.mismatched_ids
            ),
            ledger_entry=entry_report,
            ledger_entry_source=source_report,
            ledger_entry_tag=tag_report,
            blockers=blockers,
        )

    @classmethod
    def _expected(cls, facts, cases, lines, tag_views, tags, blockers):
        fact_by_id = {fact.id: fact for fact in facts}
        case_by_id = {case.id: case for case in cases}
        lines_by_case: dict[int, list[ProjectionReviewLineVO]] = {}
        for line in lines:
            case = case_by_id.get(line.case_id)
            fact = fact_by_id.get(line.bill_id)
            if not case:
                blockers.append(f"projection line {line.id}: missing confirmed case {line.case_id}")
                continue
            if not fact:
                blockers.append(f"projection line {line.id}: missing bill_fact {line.bill_id}")
                continue
            if (
                line.amount_value < 0
                or line.amount_scale != fact.amount_scale
                or line.currency_code != fact.currency_code
                or line.amount_value > fact.amount_value
            ):
                blockers.append(f"projection line {line.id}: amount conflicts with bill_fact")
                continue
            lines_by_case.setdefault(line.case_id, []).append(line)

        parent = {fact.id: fact.id for fact in facts}

        def find(value: int) -> int:
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        def union(first: int, second: int) -> None:
            first_root, second_root = find(first), find(second)
            if first_root != second_root:
                parent[max(first_root, second_root)] = min(first_root, second_root)

        valid_case_ids: set[int] = set()
        for case in cases:
            if case.review_type not in FINANCIAL_REVIEW_TYPES | ENHANCEMENT_REVIEW_TYPES:
                blockers.append(
                    f"review case {case.id}: unsupported confirmed type {case.review_type}"
                )
                continue
            case_lines = lines_by_case.get(case.id, [])
            bill_ids = sorted({line.bill_id for line in case_lines})
            if not bill_ids:
                blockers.append(f"review case {case.id}: confirmed case has no fact line")
                continue
            if case.review_type in ENHANCEMENT_REVIEW_TYPES and len(bill_ids) != 1:
                blockers.append(
                    f"review case {case.id}: enhancement case must reference exactly one fact"
                )
                continue
            if case.allocation_status not in {"COMPLETE", "PARTIAL", "CONFLICT"}:
                blockers.append(
                    f"review case {case.id}: unsupported allocation status {case.allocation_status}"
                )
                continue
            try:
                result = json.loads(case.result_json)
            except (json.JSONDecodeError, TypeError):
                blockers.append(f"review case {case.id}: result is not valid JSON")
                continue
            if not isinstance(result, dict):
                blockers.append(f"review case {case.id}: result must be a JSON object")
                continue
            valid_case_ids.add(case.id)
            if case.review_type in FINANCIAL_REVIEW_TYPES:
                for bill_id in bill_ids[1:]:
                    union(bill_ids[0], bill_id)

        facts_by_root: dict[int, list[ProjectionFactVO]] = {}
        for fact in facts:
            facts_by_root.setdefault(find(fact.id), []).append(fact)

        cases_by_root: dict[int, list[ProjectionReviewCaseVO]] = {}
        for case_id in valid_case_ids:
            case_lines = lines_by_case[case_id]
            root = find(case_lines[0].bill_id)
            cases_by_root.setdefault(root, []).append(case_by_id[case_id])

        active_views = [view for view in tag_views if view.status == "ACTIVE"]
        active_tags = {
            (tag.view_id, tag.system_name): tag
            for tag in tags
            if tag.status == "ACTIVE"
        }
        default_tags = {}
        for view in active_views:
            default = active_tags.get((view.id, "unclassified"))
            if not default:
                blockers.append(f"target tag view {view.id}: active unclassified tag is missing")
            else:
                default_tags[view.id] = default

        entries: list[LedgerEntryShadowVO] = []
        sources: list[LedgerEntrySourceShadowVO] = []
        entry_tags: list[LedgerEntryTagShadowVO] = []
        for root, component_facts in sorted(facts_by_root.items()):
            component_cases = sorted(cases_by_root.get(root, []), key=lambda item: item.id)
            financial_cases = [
                case for case in component_cases if case.review_type in FINANCIAL_REVIEW_TYPES
            ]
            if len(financial_cases) > 1:
                blockers.append(
                    f"ledger component {root}: multiple financial cases "
                    f"{[case.id for case in financial_cases]}"
                )
                continue
            financial_case = financial_cases[0] if financial_cases else None
            contributing = component_facts
            if financial_case and financial_case.review_type == "DUPLICATE":
                duplicate_lines = lines_by_case[financial_case.id]
                retained_ids = {
                    line.bill_id for line in duplicate_lines
                    if line.role == "DUPLICATE_RETAINED"
                }
                if len(retained_ids) != 1:
                    blockers.append(
                        f"review case {financial_case.id}: duplicate needs one retained fact"
                    )
                    continue
                contributing = [fact_by_id[next(iter(retained_ids))]]

            amounts = cls._cash_legs(contributing, root, blockers)
            if amounts is None:
                continue
            in_value, in_scale, in_currency, out_value, out_scale, out_currency = amounts
            accounts = cls._account_legs(
                contributing,
                component_cases,
                lines_by_case,
                root,
                blockers,
            )
            if accounts is None:
                continue
            in_account_code, out_account_code = accounts
            ledger_type = cls._ledger_type(financial_case, contributing, root, blockers)
            if ledger_type is None:
                continue
            allocation_status = (
                financial_case.allocation_status if financial_case else "DEFAULT"
            )
            title = (
                financial_case.title
                if financial_case and financial_case.title
                else component_facts[0].counterparty or component_facts[0].summary
            )
            created_time = min(fact.created_time for fact in component_facts)
            updated_time = max([
                *(fact.updated_time for fact in component_facts),
                *(case.updated_time for case in component_cases),
            ])
            input_hash = cls._input_hash(
                component_facts,
                component_cases,
                lines_by_case,
            )
            ledger_id = min(fact.id for fact in component_facts)
            entry = LedgerEntryShadowVO(
                id=ledger_id,
                created_time=created_time,
                updated_time=updated_time,
                ledger_type=ledger_type,
                allocation_status=allocation_status,
                title=title,
                start_time=min(fact.occurred_time for fact in component_facts),
                end_time=max(fact.occurred_time for fact in component_facts),
                in_amount_value=in_value,
                in_amount_scale=in_scale,
                in_currency_code=in_currency,
                out_amount_value=out_value,
                out_amount_scale=out_scale,
                out_currency_code=out_currency,
                in_account_code=in_account_code,
                out_account_code=out_account_code,
                input_hash=input_hash,
                projection_version=2,
            )
            entries.append(entry)
            for fact in component_facts:
                source_id = cls._source_row_id("BILL_FACT", fact.id, blockers)
                if source_id is not None:
                    sources.append(LedgerEntrySourceShadowVO(
                        id=source_id,
                        created_time=fact.created_time,
                        updated_time=fact.updated_time,
                        ledger_id=ledger_id,
                        source_kind="BILL_FACT",
                        source_id=fact.id,
                    ))
            for case in component_cases:
                source_id = cls._source_row_id("REVIEW_CASE", case.id, blockers)
                if source_id is not None:
                    sources.append(LedgerEntrySourceShadowVO(
                        id=source_id,
                        created_time=case.created_time,
                        updated_time=case.updated_time,
                        ledger_id=ledger_id,
                        source_kind="REVIEW_CASE",
                        source_id=case.id,
                    ))
            tag_ids = cls._component_tag_ids(
                component_facts,
                component_cases,
                lines_by_case,
                active_views,
                active_tags,
                default_tags,
                blockers,
            )
            if tag_ids is None:
                continue
            for tag_id in tag_ids:
                assignment_id = cls._tag_row_id(ledger_id, tag_id, blockers)
                if assignment_id is not None:
                    entry_tags.append(LedgerEntryTagShadowVO(
                        id=assignment_id,
                        created_time=entry.created_time,
                        updated_time=entry.updated_time,
                        ledger_id=ledger_id,
                        tag_id=tag_id,
                    ))
        return tuple(entries), tuple(sources), tuple(entry_tags)

    @classmethod
    def _component_tag_ids(
        cls,
        facts,
        cases,
        lines_by_case,
        active_views,
        active_tags,
        default_tags,
        blockers,
    ):
        if len(default_tags) != len(active_views):
            return None
        tag_case_by_bill = {}
        for case in cases:
            if case.review_type != "TAG":
                continue
            bill_id = lines_by_case[case.id][0].bill_id
            if bill_id in tag_case_by_bill:
                blockers.append(f"bill_fact {bill_id}: multiple confirmed TAG cases")
                return None
            tag_case_by_bill[bill_id] = case

        states = []
        for fact in facts:
            state = {}
            case = tag_case_by_bill.get(fact.id)
            if case:
                result = json.loads(case.result_json)
                state = result.get("tag_state", {})
                if not isinstance(state, dict) or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in state.items()
                ):
                    blockers.append(f"review case {case.id}: tag_state is invalid")
                    return None
            states.append(state)

        selected_ids = []
        for view in active_views:
            values = {
                state.get(view.system_name, "unclassified")
                for state in states
            }
            if len(values) != 1:
                blockers.append(
                    f"ledger component {min(fact.id for fact in facts)}: "
                    f"conflicting tags for view {view.system_name}"
                )
                return None
            system_name = next(iter(values))
            tag = active_tags.get((view.id, system_name))
            if not tag:
                blockers.append(
                    f"target tag view {view.id}: unknown active value {system_name}"
                )
                return None
            selected_ids.append(tag.id)
        return tuple(selected_ids)

    @staticmethod
    def _source_row_id(source_kind: str, source_id: int, blockers):
        if source_id <= 0 or source_id >= (1 << 62):
            blockers.append(f"{source_kind} source {source_id}: ID exceeds lineage range")
            return None
        return (source_id << 1) | (1 if source_kind == "REVIEW_CASE" else 0)

    @staticmethod
    def _tag_row_id(ledger_id: int, tag_id: int, blockers):
        if ledger_id <= 0 or ledger_id >= (1 << 43) or tag_id <= 0 or tag_id >= (1 << 20):
            blockers.append(
                f"ledger tag ({ledger_id}, {tag_id}): identity exceeds projection range"
            )
            return None
        return (ledger_id << 20) | tag_id

    @classmethod
    def _cash_legs(cls, facts, root, blockers):
        by_direction = {"IN": [], "OUT": []}
        for fact in facts:
            if fact.cash_direction not in by_direction or fact.amount_value < 0:
                blockers.append(f"ledger component {root}: invalid fact cash direction or amount")
                return None
            by_direction[fact.cash_direction].append(fact)

        currencies = {
            direction: {fact.currency_code for fact in direction_facts}
            for direction, direction_facts in by_direction.items()
        }
        if any(len(values) > 1 for values in currencies.values()):
            blockers.append(
                f"ledger component {root}: one cash direction contains multiple currencies"
            )
            return None
        fallback_currency = next(
            (fact.currency_code for fact in facts),
            "CNY",
        )
        fallback_scale = max((fact.amount_scale for fact in facts), default=2)
        legs = []
        for direction in ("IN", "OUT"):
            direction_facts = by_direction[direction]
            if not direction_facts:
                legs.extend((0, fallback_scale, fallback_currency))
                continue
            scale = max(fact.amount_scale for fact in direction_facts)
            value = sum(
                fact.amount_value * (10 ** (scale - fact.amount_scale))
                for fact in direction_facts
            )
            legs.extend((value, scale, direction_facts[0].currency_code))
        return tuple(legs)

    @classmethod
    def _account_legs(cls, facts, cases, lines_by_case, root, blockers):
        account_case_by_bill = {}
        for case in cases:
            if case.review_type != "ACCOUNT":
                continue
            bill_id = lines_by_case[case.id][0].bill_id
            if bill_id in account_case_by_bill:
                blockers.append(f"bill_fact {bill_id}: multiple confirmed ACCOUNT cases")
                return None
            result = json.loads(case.result_json)
            account_code = result.get("account_name")
            if not isinstance(account_code, str) or not account_code.strip():
                blockers.append(f"review case {case.id}: account_name is invalid")
                return None
            account_case_by_bill[bill_id] = account_code.strip()

        codes = {"IN": set(), "OUT": set()}
        for fact in facts:
            code = account_case_by_bill.get(fact.id, fact.account_code).strip() or "UNKNOWN"
            codes[fact.cash_direction].add(code)

        def projected(values):
            if not values:
                return "UNKNOWN"
            if len(values) > 1:
                return "MULTIPLE"
            return next(iter(values))

        if any(code == "MULTIPLE" for values in codes.values() for code in values):
            blockers.append(f"ledger component {root}: fact account code cannot be MULTIPLE")
            return None
        return projected(codes["IN"]), projected(codes["OUT"])

    @staticmethod
    def _ledger_type(financial_case, facts, root, blockers):
        if financial_case and financial_case.review_type != "DUPLICATE":
            return financial_case.review_type
        directions = {fact.cash_direction for fact in facts}
        if directions == {"IN"}:
            return "INCOME"
        if directions == {"OUT"}:
            return "EXPENSE"
        blockers.append(f"ledger component {root}: untyped facts contain mixed cash directions")
        return None

    @classmethod
    def _input_hash(cls, facts, cases, lines_by_case):
        payload = {
            "facts": [{
                "id": fact.id,
                "fact_key": fact.fact_key,
                "occurred_time": fact.occurred_time.isoformat(),
                "cash_direction": fact.cash_direction,
                "amount_value": fact.amount_value,
                "amount_scale": fact.amount_scale,
                "currency_code": fact.currency_code,
                "account_code": fact.account_code,
                "counterparty": fact.counterparty,
                "summary": fact.summary,
            } for fact in sorted(facts, key=lambda item: item.id)],
            "reviews": [{
                "id": case.id,
                "review_type": case.review_type,
                "allocation_status": case.allocation_status,
                "version": case.version,
                "result": json.loads(case.result_json),
                "lines": [asdict(line) for line in lines_by_case[case.id]],
            } for case in cases],
        }
        canonical = ReviewMatterShadowMigrationService._canonical(payload)
        return ReviewMatterShadowMigrationService._sha256(canonical)

    @staticmethod
    def _missing(expected, current):
        current_ids = {row.id for row in current}
        return tuple(row for row in expected if row.id not in current_ids)
