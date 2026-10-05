"""Budget/group planning contracts only; not financial or browser acceptance."""
from dataclasses import replace

import pytest

from backend.core.import_partition import ImportPartitionImpact, plan_import_partitions
from backend.error import TargetIntakeError


def independent(keys):
    return ImportPartitionImpact(
        review_groups=frozenset(("default", key) for key in keys),
        facts=frozenset(("new", key) for key in keys),
        outputs=frozenset(("cash", key) for key in keys),
        position_links=frozenset(("allocation", key) for key in keys))


def plan(keys, *, groups=None, targets=None, measure=independent):
    return plan_import_partitions(keys, group_keys=groups or {}, row_targets=targets, measure=measure)


def flatten(result):
    return [key for batch in result["batches"] for key in batch["selected_rows"]]


def test_2500_independent_rows_propose_three_complete_batches_without_publication():
    keys = [(1, number) for number in range(1, 2501)]
    calls = []
    def measured(component):
        calls.append(component)
        return independent(component)
    result = plan(reversed(keys), measure=measured)
    assert result["selected_count"] == 2500
    assert [len(batch["selected_rows"]) for batch in result["batches"]] == [1000, 1000, 500]
    assert flatten(result) == keys
    assert len(calls) == 2500  # Exactly one in-memory impact read per independent component.
    assert result["can_confirm"] and not result["cross_batch_atomic"]
    assert not result["blocked"]
    assert result["batches"][0]["budget"]["review_groups"] == 1000


def test_reliable_source_group_across_boundary_is_never_split():
    keys = [(1, number) for number in range(1, 1003)]
    result = plan(keys, groups={keys[999]: "reliable-source-key", keys[1000]: "reliable-source-key"})
    assert [len(batch["selected_rows"]) for batch in result["batches"]] == [999, 3]
    assert keys[999] in result["batches"][1]["selected_rows"]
    assert keys[1000] in result["batches"][1]["selected_rows"]
    assert sorted(flatten(result)) == keys


def test_explicit_row_dependencies_and_source_groups_form_one_indivisible_component():
    keys = [(1, 1), (2, 1), (3, 1), (4, 1)]
    calls = []
    def measured(component):
        calls.append(component)
        return independent(component)
    result = plan(keys, groups={keys[0]: "same-source", keys[1]: "same-source"},
                  targets={keys[2]: keys[1]}, measure=measured)
    assert calls == [tuple(keys[:3]), (keys[3],)]
    assert flatten(result) == keys


@pytest.mark.parametrize("targets", [{(1, 1): (2, 1)}, {(1, 1): (1, 1)},
    {(1, 1): (1, 2), (1, 2): (1, 3)}, {(1, 1): (1, 2), (1, 2): (1, 1)}])
def test_missing_anchor_self_chain_and_cycles_are_rejected_before_measuring(targets):
    def no_measure(_):
        pytest.fail("invalid dependencies must not reach the budget projection")
    with pytest.raises(TargetIntakeError) as error:
        plan([(1, 1), (1, 2), (1, 3)], targets=targets, measure=no_measure)
    assert error.value.code == "INVALID_EVIDENCE_TARGET"


def test_oversized_atomic_group_is_disclosed_not_dropped_or_split():
    keys = [(1, number) for number in range(1, 1003)]
    result = plan(keys, groups={key: "one-group" for key in keys[:1001]})
    assert not result["can_confirm"]
    assert flatten(result) == [keys[-1]]
    assert result["blocked"][0]["selected_rows"] == keys[:1001]
    assert result["blocked"][0]["issue"] == dict(code="INPUT_LIMIT", dimension="selected_rows", count=1001, limit=1000)
    assert set(flatten(result)) | set(result["blocked"][0]["selected_rows"]) == set(keys)


def test_compound_49_and_50_new_pairs_use_complete_defaults_plus_one_dup_group():
    keys = [(1, number) for number in range(1, 101)]
    groups = {key: ("pair", (key[1] - 1) // 2) for key in keys}
    def measured(component):
        impact = independent(component)
        # Proposed DUP identity shared by the batch; not one extra group per pair.
        return replace(impact, compound=True, review_groups=impact.review_groups | {("proposed", "batch-dup")})
    result = plan(keys, groups=groups, measure=measured)
    assert [len(batch["selected_rows"]) for batch in result["batches"]] == [98, 2]
    assert [batch["budget"]["review_groups"] for batch in result["batches"]] == [99, 3]
    assert flatten(result) == keys and result["can_confirm"]


def test_shared_existing_groups_and_outputs_are_deduplicated_not_counts_added():
    keys = [(1, 1), (1, 2)]
    def measured(component):
        return ImportPartitionImpact(compound=True, review_groups=frozenset({("existing", 8)}),
            facts=frozenset({("existing", 9)}), outputs=frozenset({("existing", 10)}),
            position_links=frozenset({("existing", 11)}), tag_changes=frozenset({("existing", 12)}))
    result = plan(keys, measure=measured)
    assert result["batches"][0]["budget"] == dict(selected_rows=2, review_groups=1,
        facts=1, outputs=1, position_links=1, tag_changes=1)


@pytest.mark.parametrize("dimension,limit", [("review_groups", 100), ("facts", 2000),
    ("outputs", 4000), ("position_links", 4000), ("tag_changes", 50000)])
def test_full_atomic_impact_boundary_blocks_only_over_limit_group(dimension, limit):
    keys = [(1, 1), (1, 2)]
    def measured(component):
        count = limit + (component[0] == keys[1])
        return replace(ImportPartitionImpact(compound=True), **{dimension: frozenset(range(count))})
    result = plan(keys, measure=measured)
    assert flatten(result) == keys[:1]
    assert not result["can_confirm"]
    issue = result["blocked"][0]["issue"]
    assert issue["dimension"] == dimension and issue["count"] == limit + 1 and issue["limit"] == limit
    assert issue["code"] == ("TAG_IMPACT_LIMIT" if dimension == "tag_changes" else "REVIEW_CHANGE_LIMIT")


def test_mixed_pure_and_compound_groups_cannot_use_pure_default_exemption():
    keys = [(1, number) for number in range(1, 104)]
    def measured(component):
        return replace(independent(component), compound=component[0] == keys[-1])
    result = plan(keys, measure=measured)
    assert [len(batch["selected_rows"]) for batch in result["batches"]] == [102, 1]
    assert [batch["compound"] for batch in result["batches"]] == [False, True]


@pytest.mark.parametrize("keys", [[], [(1, 1), (1, 1)], [(True, 1)], [(1, 0)], [(1, -1)],
    [[1, 1]], [(1, [])], ["1:1"]])
def test_invalid_frozen_selection_is_rejected(keys):
    with pytest.raises(TargetIntakeError) as error:
        plan(keys)
    assert error.value.code == "INPUT_LIMIT"


def test_pure_import_tag_budget_causes_informed_partition_not_review_group_cap():
    keys = [(1, number) for number in range(1, 61)]
    def measured(component):
        key = component[0]
        return replace(independent(component), tag_changes=frozenset((key, view) for view in range(1000)))
    result = plan(keys, measure=measured)
    assert [len(batch["selected_rows"]) for batch in result["batches"]] == [50, 10]
    assert [batch["budget"]["tag_changes"] for batch in result["batches"]] == [50000, 10000]
    assert flatten(result) == keys and result["can_confirm"]


def test_plan_is_deterministic_and_does_not_mutate_frozen_inputs():
    keys = [(1, number) for number in range(1, 1003)]
    groups = {keys[0]: "origin", keys[-1]: "origin"}
    targets = {keys[800]: keys[700]}
    original_groups, original_targets = dict(groups), dict(targets)
    expected = plan(keys, groups=groups, targets=targets)
    assert plan(reversed(keys), groups=groups, targets=targets) == expected
    assert groups == original_groups and targets == original_targets
    assert keys == [(1, number) for number in range(1, 1003)]
    assert len(set(flatten(expected))) == len(keys)
    assert all(batch["budget"]["selected_rows"] <= 1000 for batch in expected["batches"])


def test_caller_business_error_is_not_swallowed_as_an_excludable_budget_issue():
    def broken(_):
        raise TargetIntakeError(409, "broken financial relationship", code="RELATION_BROKEN")
    with pytest.raises(TargetIntakeError) as error:
        plan([(1, 1)], measure=broken)
    assert error.value.code == "RELATION_BROKEN"


def test_selection_cap_does_not_become_single_transaction_cap():
    keys = [(1, number) for number in range(1, 20001)]
    result = plan(keys)
    assert len(result["batches"]) == 20 and len(flatten(result)) == 20000
    with pytest.raises(TargetIntakeError) as error:
        plan(keys + [(1, 20001)])
    assert error.value.code == "INPUT_LIMIT"
