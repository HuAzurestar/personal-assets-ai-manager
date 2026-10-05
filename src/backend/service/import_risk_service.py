"""Bounded page/batch risk hints across the entire current preview context."""
from collections import defaultdict

from backend.core.import_risk import risk_signature, hint_scope, require_new_risk_confirmation
from backend.core.import_evidence import target_locator
from backend.mapper.import_batch_mapper import fingerprint, fail
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper


class ImportRiskService:
    def __init__(self, mapper):
        # Reuse the caller's mapper/Session/deadline when this becomes part of
        # preflight or locked revalidation. Do not start a financial transaction.
        self.mapper = mapper

    @staticmethod
    def validate_new_cash(candidate, choice, risk):
        # A persisted Fact or a proven manual link adds only source evidence.
        # DUP's separate source/financial planner grants its own eligibility;
        # its new real ROW keeper is checked independently in the same scope.
        if not candidate["fact_id"] and not candidate.get("evidence_link") and not candidate.get("duplicate_plan"):
            require_new_risk_confirmation(choice, risk["hint"])

    def plan(self, rows, candidates, requested):
        keys = list(requested)
        if not 1 <= len(rows) <= 20000 or set(rows) != set(candidates) or not 1 <= len(keys) <= 20000:
            fail("INPUT_LIMIT", 422)
        for key in keys:
            if not isinstance(key, tuple) or len(key) != 2:
                fail("INVALID_EVIDENCE_TARGET", 422)
            target_locator(dict(kind="ROW", file_id=key[0], source_row_number=key[1]))
            if key not in rows:
                fail("PREVIEW_ROW_NOT_FOUND", 404)
        if len(set(keys)) != len(keys):
            fail("INPUT_LIMIT", 422)
        relations = TrustedRelationMapper(self.mapper.db)
        relations.read_snapshot()
        with self.mapper.match_budget():
            relations.validate()
            signatures = {value for key in keys if (value := risk_signature(candidates[key]["values"])) is not None}
            facts = self.mapper.signature_facts(signatures)
            by_id = {fact["id"]: fact["fact_key"] for fact in facts}
            scopes = defaultdict(set)
            for fact in facts:
                scopes[risk_signature(fact)].add(fact["fact_key"])
            own = {}
            # Whole-preview O(R) local index: another page/file is not invisible.
            # Coalesce by immutable canonical key, not row count or guessed IDs.
            # This also survives approved earlier batches acquiring real IDs.
            for key, candidate in candidates.items():
                signature = risk_signature(candidate["values"])
                if signature not in signatures:
                    continue
                identity = candidate["values"]["fact_key"]
                if candidate.get("fact_id"):
                    if candidate["fact_id"] not in by_id:
                        fail("RELATION_BROKEN")
                    identity = by_id[candidate["fact_id"]]
                elif candidate.get("evidence_link"):
                    kind, target = candidate["evidence_link"]["identity"]
                    if kind == "FACT":
                        if target not in by_id:
                            fail("RELATION_BROKEN")
                        identity = by_id[target]
                    else:
                        identity = target
                own[key] = identity
                scopes[signature].add(identity)
            # Hash each complete signature scope once, not once per row. No
            # O(R²) set copies when 20k rows have the same accounting core.
            scope_hashes = {signature: fingerprint(dict(signature=list(signature), identities=sorted(identities)))
                for signature, identities in scopes.items()}
            result = {}
            for key in keys:
                values = candidates[key]["values"]
                scope = hint_scope(rows[key], values)
                signature = risk_signature(values)
                if signature is None:
                    hint = dict(state="UNCHECKED", scope=scope, candidate_count=None, reason_codes=["ROW_INVALID"])
                    premise = dict(signature=None, scope_hash=None, own_identity=None)
                else:
                    identities = scopes[signature]
                    count = len(identities) - int(own[key] in identities)
                    state = "SUSPECTED" if count else "NONE_IN_SCOPE" if scope["source_known"] else "UNCHECKED"
                    reasons = ["EXACT_CORE_CANDIDATE"] if count else ["EXACT_SCOPE_ONLY"]
                    if not scope["source_known"]:
                        reasons.append("SOURCE_IDENTITY_REQUIRED")
                    hint = dict(state=state, scope=scope, candidate_count=None if state == "UNCHECKED" else count,
                        reason_codes=reasons)
                    premise = dict(signature=list(signature), scope_hash=scope_hashes[signature], own_identity=own[key])
                result[key] = dict(hint=hint, premise_hash=fingerprint(dict(hint=hint, premise=premise)))
            return result
