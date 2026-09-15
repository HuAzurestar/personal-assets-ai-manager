from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from backend.mapper.target_tag_projection_mapper import (
    ActiveTagValue,
    TargetTagProjectionMapper,
)


class TargetTagProjectionService:
    """Maintain one direct effective Tag value per active View and Ledger."""

    def __init__(self, db: Session):
        self.mapper = TargetTagProjectionMapper(db)

    def sync_ledgers(self, ledger_ids: list[int]) -> None:
        ledger_ids = list(dict.fromkeys(ledger_ids))
        if not ledger_ids:
            return
        dictionary = self.mapper.active_dictionary()
        defaults, tag_ids = self._dictionary_maps(dictionary)
        current = self.mapper.current_states(ledger_ids)
        resolved: dict[int, tuple[int, ...]] = {}
        for ledger_id in ledger_ids:
            state = current.get(ledger_id, {})
            resolved[ledger_id] = tuple(
                tag_ids[(view_name, state.get(view_name, default_name))]
                for view_name, default_name in sorted(defaults.items())
                if (view_name, state.get(view_name, default_name)) in tag_ids
            )
        self.mapper.replace(resolved)

    def sync_all(self) -> None:
        self.sync_ledgers(self.mapper.active_ledger_ids())

    def assignment(
        self,
        state: dict[str, str],
    ) -> tuple[dict[str, str], tuple[int, ...]]:
        dictionary = self.mapper.active_dictionary()
        defaults, tag_ids = self._dictionary_maps(dictionary)
        expected = set(defaults)
        submitted = set(state)
        if submitted != expected:
            missing = sorted(expected - submitted)
            unknown = sorted(submitted - expected)
            raise ValueError(
                f"tag_state must contain every active view; "
                f"missing={missing}, unknown={unknown}"
            )
        invalid = sorted(
            f"{view}:{value}"
            for view, value in state.items()
            if (view, value) not in tag_ids
        )
        if invalid:
            raise ValueError(f"unknown or archived tag values: {invalid}")
        normalized = {view: state[view] for view in sorted(state)}
        return normalized, tuple(
            tag_ids[(view, normalized[view])] for view in normalized
        )

    @staticmethod
    def _dictionary_maps(
        dictionary: tuple[ActiveTagValue, ...],
    ) -> tuple[dict[str, str], dict[tuple[str, str], int]]:
        values: dict[str, set[str]] = defaultdict(set)
        tag_ids: dict[tuple[str, str], int] = {}
        defaults: dict[str, str] = {}
        for item in dictionary:
            values[item.view_system_name].add(item.tag_system_name)
            tag_ids[(item.view_system_name, item.tag_system_name)] = item.tag_id
            if item.tag_system_name == "unclassified":
                defaults[item.view_system_name] = item.tag_system_name
        missing_defaults = sorted(set(values) - set(defaults))
        if missing_defaults:
            raise ValueError(
                f"active tag views have no active unclassified value: {missing_defaults}"
            )
        return defaults, tag_ids
