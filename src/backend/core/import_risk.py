"""Exact-scope hints and explicit new-cash consent, not duplicate decisions."""
from collections import defaultdict
from backend.core.import_identity import SOURCE_CODES, source_account_code, same_fact
from backend.core.import_evidence import complete_source_code
from backend.error import TargetIntakeError


def risk_signature(values):
    return tuple(values[key] for key in ("occurred_time", "cash_direction", "amount", "currency_code")) if values else None


def hint_scope(row, values):
    return dict(occurred_time=values["occurred_time"] if values else None,
        currency_code=values["currency_code"] if values else None,
        cash_direction=("IN" if values["cash_direction"] == 1 else "OUT") if values else None,
        source_known=bool(SOURCE_CODES.get(row.get("source_type"), 0) and
            complete_source_code(SOURCE_CODES[row["source_type"]],source_account_code(row))))


def canonical_duplicate_groups(rows, candidates):
    """Whole-preview reliable identities, never inferred from a filtered page.

    A shared accounting core/raw hash is insufficient. This only projects the
    already-confirmed canonical reference identity; no choice or Fact changes.
    Sorting file ID then original row number gives a stable first occurrence.
    """
    identities = defaultdict(list)
    for key, candidate in candidates.items():
        if candidate.get("values"):
            identities[candidate["values"]["fact_key"]].append(key)
    result = {}
    for members in identities.values():
        if len(members) < 2:
            continue
        first = min(members)
        original = candidates[first]["values"]
        if any(candidates[key].get("issue") or not rows[key].get("reference") or
                not hint_scope(rows[key], candidates[key]["values"])["source_known"] or
                not same_fact(original, candidates[key]["values"]) for key in members):
            continue
        keeper = dict(file_id=first[0], source_row_number=first[1])
        for key in members:
            result[key] = dict(kind="SOURCE_REFERENCE", keeper_row=keeper,
                member_count=len(members), is_keeper=key == first)
    return result


def default_import_decision(candidate, hint, group):
    """A recommendation, not saved user intent or permission to publish cash."""
    if candidate.get("issue") or candidate["classification"] not in {"NEW", "EXISTING", "PROCESSED"}:
        return "SKIP"
    if group and not group["is_keeper"] and candidate["classification"] != "PROCESSED":
        return "SKIP"
    if candidate["classification"] != "NEW":
        return "ACCEPT"
    return "ACCEPT" if (hint.get("state") == "NONE_IN_SCOPE" and type(hint.get("candidate_count")) is int and
        hint["candidate_count"] == 0 and hint.get("scope", {}).get("source_known") is True) else "SKIP"


def require_new_risk_confirmation(choice, hint):
    """Called for a validated, prospective new real Fact; never auto-acknowledge.

    Manual pairs still require their source/final-set planner. This check
    grants no target eligibility, publication or execution authority itself.
    """
    if choice.get("decision") != "ACCEPT":
        return
    state, count = hint.get("state"), hint.get("candidate_count")
    if not (state == "UNCHECKED" and count is None or state == "NONE_IN_SCOPE" and type(count) is int and count == 0 or
            state == "SUSPECTED" and type(count) is int and count > 0):
        raise TargetIntakeError(422, "risk scope must be rechecked before new cash", code="IMPORT_REVIEW_REQUIRED")
    resolution = choice.get("resolution", "AUTO")
    if resolution not in {"AUTO", "NEW", "LINK_EXISTING", "DUPLICATE"}:
        raise TargetIntakeError(422, "invalid import resolution", code="INVALID_EVIDENCE_TARGET")
    if resolution in {"LINK_EXISTING", "DUPLICATE"}:
        return
    if resolution == "AUTO" and choice.get("acknowledge_new_risk"):
        raise TargetIntakeError(422, "new cash requires the explicit NEW choice", code="IMPORT_REVIEW_REQUIRED")
    if state != "NONE_IN_SCOPE" and (resolution != "NEW" or choice.get("acknowledge_new_risk") is not True):
        raise TargetIntakeError(422, "inspect candidates or explicitly confirm additional new cash", code="IMPORT_REVIEW_REQUIRED",
            details=dict(action="REVIEW_DUPLICATE_CANDIDATES", risk_state=hint["state"], candidate_count=hint["candidate_count"]))
