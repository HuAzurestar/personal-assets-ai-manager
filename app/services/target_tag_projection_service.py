from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from app.mappers.target_tag_projection_mapper import TargetTagProjectionMapper


class TargetTagProjectionService:
    """Resolve per-Fact TAG reviews into one deterministic ledger tag state."""

    def __init__(self, db: Session):
        self.mapper = TargetTagProjectionMapper(db)

    def sync(self, fact_ledgers: dict[int, int]) -> None:
        if not fact_ledgers:
            return
        by_ledger: dict[int, list[int]] = defaultdict(list)
        for fact_id, ledger_id in fact_ledgers.items():
            by_ledger[ledger_id].append(fact_id)
        self.sync_economics(by_ledger)

    def sync_economics(self, economic_facts: dict[int, list[int]]) -> None:
        if not economic_facts:
            return
        dictionary = self.mapper.active_dictionary()
        defaults: dict[str, str] = {}
        tag_ids: dict[tuple[str, str], int] = {}
        views: set[str] = set()
        for item in dictionary:
            views.add(item.view_system_name)
            tag_ids[(item.view_system_name, item.tag_system_name)] = item.tag_id
            if item.tag_system_name == "unclassified":
                defaults[item.view_system_name] = "unclassified"
        missing_defaults = sorted(views - set(defaults))
        if missing_defaults:
            raise ValueError(
                f"active tag views have no active unclassified value: {missing_defaults}"
            )

        fact_ids = sorted({
            fact_id for values in economic_facts.values() for fact_id in values
        })
        reviewed = self.mapper.confirmed_states(fact_ids)

        resolved: dict[int, tuple[int, ...]] = {}
        for ledger_id, fact_ids in economic_facts.items():
            selected_ids = []
            for view_name in sorted(views):
                values = set()
                for fact_id in fact_ids:
                    selected = reviewed.get(fact_id, {}).get(
                        view_name, defaults[view_name]
                    )
                    # Archived values cease to publish but remain in Review history.
                    if (view_name, selected) not in tag_ids:
                        selected = defaults[view_name]
                    values.add(selected)
                if len(values) != 1:
                    raise ValueError(
                        f"ledger {ledger_id} cannot merge facts with different "
                        f"{view_name} tags: {sorted(values)}"
                    )
                selected_ids.append(tag_ids[(view_name, values.pop())])
            resolved[ledger_id] = tuple(selected_ids)
        self.mapper.replace(resolved)

    def sync_all(self) -> None:
        self.sync(self.mapper.all_fact_ledgers())

    def validate_complete(self, state: dict[str, str]) -> dict[str, str]:
        dictionary = self.mapper.active_dictionary()
        allowed: dict[str, set[str]] = defaultdict(set)
        for item in dictionary:
            allowed[item.view_system_name].add(item.tag_system_name)
        expected = set(allowed)
        submitted = set(state)
        if submitted != expected:
            missing = sorted(expected - submitted)
            unknown = sorted(submitted - expected)
            raise ValueError(
                f"tag_state must contain every active view; missing={missing}, unknown={unknown}"
            )
        invalid = sorted(
            f"{view}:{value}"
            for view, value in state.items()
            if value not in allowed[view]
        )
        if invalid:
            raise ValueError(f"unknown or archived tag values: {invalid}")
        return {view: state[view] for view in sorted(state)}
