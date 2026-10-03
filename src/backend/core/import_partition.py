"""Plan informed, serial import batches without writing or accepting any row.

The Service supplies verified source groups and complete publication impacts.
No fuzzy matching, SQL, receipt, or execution authority belongs in this planner.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Hashable

from backend.error import TargetIntakeError


MAX_SELECTION = 20_000
MAX_BATCH_ROWS = 1_000
COMPOUND_LIMITS = {"review_groups": 100, "facts": 2_000, "outputs": 4_000,
                   "position_links": 4_000, "tag_changes": 50_000}


@dataclass(frozen=True)
class ImportPartitionImpact:
    """Deduplicable, full-scope identities, not estimated per-row counts.

    Existing identities and proposed identities must occupy separate namespaces.
    ``position_links`` accounts for both allocation stages, as in SOL-010.
    """
    compound: bool = False
    review_groups: frozenset[Hashable] = field(default_factory=frozenset)
    facts: frozenset[Hashable] = field(default_factory=frozenset)
    outputs: frozenset[Hashable] = field(default_factory=frozenset)
    position_links: frozenset[Hashable] = field(default_factory=frozenset)
    tag_changes: frozenset[Hashable] = field(default_factory=frozenset)


def _invalid_target():
    raise TargetIntakeError(422, "ROW targets must be selected, real anchors without chains",
                            code="INVALID_EVIDENCE_TARGET")


def _components(selected_rows, group_keys, row_targets):
    """Union verified source identities and explicit ROW dependencies in O(R+E)."""
    parent = {key: key for key in selected_rows}
    size = dict.fromkeys(selected_rows, 1)

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def join(left, right):
        left, right = root(left), root(right)
        if left == right:
            return
        if size[left] < size[right]:
            left, right = right, left
        parent[right] = left
        size[left] += size[right]

    if set(group_keys) - set(parent) or set(row_targets) - set(parent):
        _invalid_target()
    first = {}
    for key in selected_rows:
        identity = group_keys.get(key)
        # Only caller-verified source identities are eligible to join rows.
        # None denotes an independent row; amount or merchant is never a key.
        if identity is not None:
            if identity in first:
                join(key, first[identity])
            else:
                first[identity] = key
        target = row_targets.get(key)
        if target is not None:
            if target not in parent or target == key or target in row_targets:
                _invalid_target()
            join(key, target)
    members = defaultdict(list)
    for key in selected_rows:
        members[root(key)].append(key)
    return sorted(members.values(), key=lambda keys: keys[0])


def _limit_issue(row_count, compound, counts):
    if row_count > MAX_BATCH_ROWS:
        return dict(code="INPUT_LIMIT", dimension="selected_rows",
                    count=row_count, limit=MAX_BATCH_ROWS)
    limits = COMPOUND_LIMITS if compound else {"tag_changes": 50_000}
    for dimension, limit in limits.items():
        if counts[dimension] > limit:
            return dict(code="TAG_IMPACT_LIMIT" if dimension == "tag_changes" else "REVIEW_CHANGE_LIMIT",
                        dimension=dimension, count=counts[dimension], limit=limit)
    return None


def plan_import_partitions(selected_rows, *, group_keys, measure, row_targets=None):
    """Return complete proposed batches and blockers; never silently drop rows.

    ``measure(component)`` is called exactly once per indivisible component.
    It must include original defaults, current keeper groups, both link stages,
    and all synchronous tag effects. Only the owning Service can derive this
    impact and the later financial effects. The returned plan is not permission
    to execute: the user must see/confirm it, and every write revalidates state.
    """
    keys = list(selected_rows)
    if (not 1 <= len(keys) <= MAX_SELECTION
            or any(not isinstance(key, tuple) or len(key) != 2
                   or any(type(value) is not int or value <= 0 for value in key) for key in keys)
            or len(set(keys)) != len(keys)):
        raise TargetIntakeError(422, "choose 1..20000 unique source-row identities", code="INPUT_LIMIT")
    keys.sort()
    components = _components(keys, group_keys, row_targets or {})
    batches, blocked = [], []
    current_rows, current_compound = [], False
    current_sets = {name: set() for name in COMPOUND_LIMITS}

    def emit():
        nonlocal current_rows, current_compound, current_sets
        if current_rows:
            batches.append(dict(selected_rows=sorted(current_rows), compound=current_compound,
                budget=dict(selected_rows=len(current_rows), **{name: len(values) for name, values in current_sets.items()})))
        current_rows, current_compound = [], False
        current_sets = {name: set() for name in COMPOUND_LIMITS}

    for component in components:
        impact = measure(tuple(component))
        standalone = {name: len(getattr(impact, name)) for name in COMPOUND_LIMITS}
        issue = _limit_issue(len(component), impact.compound, standalone)
        if issue:
            blocked.append(dict(selected_rows=component,
                budget=dict(selected_rows=len(component), **standalone), issue=issue))
            continue
        # Add only unseen identities. The complete impact is measured once,
        # not recomputed for every trial prefix or queried once per source row.
        additions = {name: set(getattr(impact, name)) - current_sets[name] for name in COMPOUND_LIMITS}
        combined = {name: len(current_sets[name]) + len(additions[name]) for name in COMPOUND_LIMITS}
        compound = current_compound or impact.compound
        if _limit_issue(len(current_rows) + len(component), compound, combined):
            emit()
            additions = {name: set(getattr(impact, name)) for name in COMPOUND_LIMITS}
            compound = impact.compound
        current_rows.extend(component)
        current_compound = compound
        for name, values in additions.items():
            current_sets[name].update(values)
    emit()
    return dict(selected_count=len(keys), batches=batches, blocked=blocked,
                can_confirm=not blocked, cross_batch_atomic=False)
