"""Exact-scope hints and explicit new-cash consent, not duplicate decisions."""
from backend.core.import_identity import SOURCE_CODES, source_account_code
from backend.error import TargetIntakeError


def risk_signature(values):
    return tuple(values[key] for key in ("occurred_time", "cash_direction", "amount", "currency_code")) if values else None


def hint_scope(row, values):
    return dict(occurred_time=values["occurred_time"] if values else None,
        currency_code=values["currency_code"] if values else None,
        cash_direction=("IN" if values["cash_direction"] == 1 else "OUT") if values else None,
        source_known=bool(SOURCE_CODES.get(row.get("source_type"), 0) and source_account_code(row)))


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
