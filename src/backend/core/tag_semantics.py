"""Ephemeral full-semantic matching, never a persisted tag revision/history."""
from collections import defaultdict


def position_description(identity, position, leg, link):
    return (identity, position["type"], position["usage_scenario"], position["unit_code"],
            leg["type"], leg["leg_direction"], leg["leg_amount"], leg["occurred_time"],
            leg["basis"], leg.get("source_position_leg_id", leg.get("source", 0)),
            link["cash_amount"], link["cash_currency_code"])


def output_key(transaction_id, entry_type, fact, amount, ref, behavior, descriptions):
    return (transaction_id, entry_type, fact["cash_direction"], amount, fact["currency_code"],
            ref, fact["occurred_time"], behavior, tuple(sorted(descriptions, key=repr)))


def tag_effect(bundle, closing, drafts, facts, positions, dictionary, tags, request_count, scan_available):
    """Hash buckets with tuple equality; only unique old and new outputs match."""
    reviews = {row["id"]: row for row in bundle["reviews"]}
    ledgers = {row["id"]: row for row in bundle["ledger_entries"]}
    legs = {row["id"]: row for row in bundle["position_legs"]}
    old_links = defaultdict(list)
    for link in bundle["position_allocations"]:
        leg = legs[link["position_leg_id"]]
        old_links[link["ledger_id"]].append(position_description(
            leg["position_id"], positions[leg["position_id"]], leg, link))
    old, new = defaultdict(list), defaultdict(list)
    closing_ledgers = set()
    for allocation in bundle["allocations"]:
        if allocation["review_id"] not in closing:
            continue
        ledger = ledgers[allocation["ledger_id"]]
        closing_ledgers.add(ledger["id"])
        key = output_key(allocation["transaction_id"], ledger["entry_type"],
                         facts[allocation["transaction_id"]], ledger["cash_amount"],
                         ledger["account_ref_id"], reviews[allocation["review_id"]]["behavior_type"],
                         old_links[ledger["id"]])
        old[key].append(ledger["id"])
    outputs = []
    for ri, draft in enumerate(drafts):
        descriptions = defaultdict(list)
        for link in draft["position_allocations"]:
            leg = draft["legs"][link["leg_index"]]
            pid = leg["existing_position_id"]
            identity = pid if pid is not None else ("NEW", ri, leg["new_position_index"])
            position = positions[pid] if pid is not None else draft["new_positions"][leg["new_position_index"]]
            descriptions[link["allocation_index"]].append(position_description(identity, position, leg, link))
        for ai, allocation in enumerate(draft["allocations"]):
            output = (ri, ai)
            outputs.append(output)
            if allocation["entry_type"] != 3:
                key = output_key(allocation["transaction_id"], allocation["entry_type"],
                                 facts[allocation["transaction_id"]], allocation["cash_amount"],
                                 allocation["account_ref_id"], draft["type"], descriptions[ai])
                new[key].append(output)
    matches = {values[0]: old[key][0] for key, values in new.items()
               if len(values) == 1 and len(old.get(key, ())) == 1}
    by_tag = {row["id"]: row for row in dictionary}
    defaults = {row["view_id"]: row["id"] for row in dictionary if row["system_name"] == "unclassified"}
    values = defaultdict(dict)
    for tag in tags:
        active = by_tag.get(tag["tag_id"])
        if active and tag["ledger_id"] in closing_ledgers:
            values[tag["ledger_id"]][active["view_id"]] = tag["tag_id"]
    mappings, carried = [], set()
    # Plain creation without old content need not enumerate default relationships.
    if closing_ledgers:
        for ri, ai in outputs:
            old_id = matches.get((ri, ai), 0)
            for view_id, default_id in sorted(defaults.items()):
                tag_id = values[old_id].get(view_id, default_id)
                keep = bool(old_id and view_id in values[old_id])
                if keep:
                    carried.add((old_id, view_id))
                mappings.append(dict(old_ledger_id=old_id if keep else 0,
                    new_output=dict(review_index=ri, allocation_index=ai), view_id=view_id,
                    tag_id=tag_id, disposition="KEEP" if keep else "REVIEW_REQUIRED"))
        for old_id, state in sorted(values.items()):
            for view_id, tag_id in sorted(state.items()):
                if (old_id, view_id) not in carried:
                    mappings.append(dict(old_ledger_id=old_id, new_output=None, view_id=view_id,
                                         tag_id=tag_id, disposition="RETAIN_INACTIVE"))
    used_tags = sorted({row["tag_id"] for row in mappings})
    labels = [dict(tag_id=tag_id, view_id=by_tag[tag_id]["view_id"],
        tag_name=by_tag[tag_id].get("tag_name", ""), view_name=by_tag[tag_id].get("view_name", ""))
        for tag_id in used_tags]
    return dict(invalidated_request_count=request_count, mappings=mappings, mapping_labels=labels,
                scan_state=("PENDING" if scan_available else "UNAVAILABLE")
                if closing_ledgers or outputs else "NOT_NEEDED")
