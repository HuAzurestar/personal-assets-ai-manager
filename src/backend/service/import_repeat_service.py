"""Readonly suspected export groups; applying them requires explicit consent.

No keyless identity is merged, no choice is saved, and no cash is published.
The eventual draft uses the existing NEW/SKIP and full confirmation contracts.
"""
from collections import defaultdict, Counter

from backend.core.import_identity import source_account_code
from backend.core.import_risk import risk_signature
from backend.core.source_account_identity import reliable_source
from backend.core.import_public_text import masked_summary
from backend.mapper.import_match_mapper import ImportMatchMapper
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.service.import_confirmation_service import source_label, row_locator


def repeat_scope_key(row, candidate):
    source = reliable_source(row)
    values = candidate.get("values")
    if row.get("reference") or not source or source[1] != source_account_code(row) or not values:
        return None
    return source + risk_signature(values)


class ImportRepeatService:
    def __init__(self, db):
        self.mapper = ImportMatchMapper(db)

    def propose(self, state, choices):
        relations = TrustedRelationMapper(self.mapper.db)
        relations.read_snapshot()
        with self.mapper.match_budget():
            relations.validate()
            rows = {key:{field:value for field,value in state.rows[key].items()
                if field not in {"_group_issue", "_group_token"}} for key in choices}
            # Retained explicit intent is reported, not run as another command.
            basic = {key:{field:choice[field] for field in ("decision", "recheck")} for key,choice in choices.items()}
            current = self.mapper._match(rows, basic, target_limit=20000)
            signatures = {signature for candidate in current.values()
                if (signature := risk_signature(candidate["values"])) is not None}
            # Count every accepted exact-core candidate, including unknown
            # origins. Do not drop one to fabricate a safe new first occurrence.
            accepted = {risk_signature(fact) for fact in self.mapper.signature_facts(signatures)}
            full = state.candidates | current
            scopes = defaultdict(list)
            for key,candidate in full.items():
                scope = repeat_scope_key(state.rows[key], candidate)
                if scope:
                    scopes[scope].append(key)
            items = {}
            for key,candidate in current.items():
                stored,choice = candidate["stored"],choices[key]
                reason = ("ROWS_ALREADY_PROCESSED" if stored and stored["row_status"] == 1 else
                    "ROW_RECHECK_REQUIRED" if stored and stored["row_status"] in {2,3} and not choice["recheck"] else
                    "USER_INTENT_RETAINED" if choice["resolution"] != "AUTO" else
                    (candidate["issue"] or
                    ("EXTERNAL_CANDIDATE_REQUIRES_REVIEW" if candidate["fact_id"] else None)))
                if not reason and not rows[key].get("reference") and repeat_scope_key(rows[key],candidate) is None:
                    reason = "SOURCE_IDENTITY_REQUIRED"
                items[key] = dict(row=row_locator(key), state="EXCEPTION" if reason else "UNCHANGED",
                    reason_codes=[reason] if reason else ["CANONICAL_IDENTITY_RETAINED" if rows[key].get("reference") else "NO_REPEAT_IN_SCOPE"],
                    keeper_row=None)
            groups = []
            for members in scopes.values():
                selected = [key for key in members if key in choices]
                if len(members) < 2 or not selected:
                    continue
                reason = ("REPEAT_SCOPE_INCOMPLETE" if len(selected) != len(members) else
                    "IDENTITY_AMBIGUOUS" if any(count > 1 for count in Counter(key[0] for key in members).values()) else
                    "EXTERNAL_CANDIDATE_REQUIRES_REVIEW" if risk_signature(full[members[0]]["values"]) in accepted else
                    "GROUP_REQUIRES_REVIEW" if any(items[key]["state"] == "EXCEPTION" for key in selected) else None)
                if reason:
                    for key in selected:
                        if items[key]["state"] != "EXCEPTION":
                            items[key].update(state="EXCEPTION",reason_codes=[reason])
                    continue
                keeper = min(members)
                values = current[keeper]["values"]
                group = dict(kind="SUSPECTED_EXPORT", keeper_row=row_locator(keeper),
                    repeated_rows=[row_locator(key) for key in sorted(members) if key != keeper],
                    parsed=dict(occurred_time=values["occurred_time"],amount=values["amount"],
                        currency_code=values["currency_code"],cash_direction="IN" if values["cash_direction"] == 1 else "OUT",
                        summary=masked_summary(values["summary"])), source_label_masked=source_label(rows[keeper]))
                groups.append(group)
                for key in selected:
                    items[key].update(state="GROUPED",reason_codes=[],keeper_row=row_locator(keeper))
            return dict(selected_count=len(choices), groups=sorted(groups,key=lambda group:(
                group["keeper_row"]["file_id"],group["keeper_row"]["source_row_number"])),
                items=[items[key] for key in sorted(items)])
