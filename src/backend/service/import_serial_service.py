"""Per-child frozen approval guards and strictly certified own-effect rebasing.

This helper never commits, starts a transaction, dispatches a child, or keeps
results. The import caller owns the financial transaction and bounded preview.
Only exact changes observed inside its successful write can be normalized to
the human-approved symbolic baseline. External changes still change the digest.
"""
from copy import deepcopy

from backend.core.import_identity import canonical_json
from backend.mapper.import_batch_mapper import fail, fingerprint


class ImportSerialService:
    def __init__(self, mapper, state):
        self.mapper, self.state = mapper, state
        self.guard = state.operation
        self.targets = set(self.guard["targets"]) if self.guard else set()

    def child(self, payload, order):
        guard = self.guard
        if (not guard or guard["stopped"] or guard["digest"] != payload.operation_preview_digest or
                guard["next"] != payload.batch_index or guard["next"] >= len(guard["batches"])):
            fail("STALE_PREVIEW")
        child = guard["batches"][guard["next"]]
        expected = [(row["file_id"], row["source_row_number"]) for row in child["rows"]]
        if sorted(order) != expected or payload.batch_preview_digest != child["digest"]:
            fail("STALE_PREVIEW")
        return child

    def _normalize(self, value):
        if isinstance(value, list):
            return [self._normalize(item) for item in value]
        if not isinstance(value, dict):
            return value
        if set(value) == {"ref_id", "auto_identity", "chain", "issue"}:
            owned = self.guard["refs"].get(str(value["ref_id"]))
            if (owned and value["auto_identity"] == [owned["source_namespace"], owned["source_identity"]] and
                    value["chain"] == [owned] and value["issue"] is None):
                return dict(ref_id=0, auto_identity=value["auto_identity"], chain=[], issue=None)
            return deepcopy(value)
        if {"fact", "originals", "evidence"} <= set(value):
            owned = self.guard["proofs"].get(str(value["fact"]["id"]), {})
            actual = {str(row["row_id"]): row for row in value["evidence"]}
            if any(actual.get(id) != proof for id, proof in owned.items()):
                # Missing, relinked or rewritten own evidence is not our append.
                fail("STALE_PREVIEW")
            return {name: [deepcopy(row) for row in rows if str(row["row_id"]) not in owned]
                    if name in {"originals", "evidence"} else self._normalize(rows)
                    for name, rows in value.items()}
        return {name: self._normalize(item) for name, item in value.items()}

    def candidates(self, current, choices):
        normalized = {}
        for key, candidate in current.items():
            if candidate["premise_hash"] == self.state.candidates[key]["premise_hash"]:
                # No approved effect touched this row. Do not serialize and
                # hash 1000 unchanged source premises twice under the lock.
                normalized[key] = candidate
                continue
            item = candidate.copy()
            for field in ("premise", "account", "evidence_link", "duplicate_plan"):
                if field in item:
                    item[field] = self._normalize(item[field])
            item["premise_hash"] = fingerprint(item["premise"] | dict(
                classification=item["classification"], issue=item["issue"], values=item["values"],
                choice=choices.get(key), parser=self.state.rows[key].get("_document", {}),
                group=self.state.rows[key].get("_group_token")))
            if item["premise_hash"] != self.state.candidates[key]["premise_hash"]:
                fail("STALE_PREVIEW")
            normalized[key] = item
        return normalized

    def rules(self, rows):
        originals = {row["id"]: row for row in self.guard["rules"]}
        result = []
        for row in rows:
            owned = self.guard["rule_effects"].get(str(row["id"]))
            if owned:
                if row != owned:
                    fail("STALE_PREVIEW")
                result.append(originals[row["id"]])
            else:
                result.append(row)
        return result

    def record(self, current, result, before_rules):
        """Capture bounded exact effects before the transaction's only commit."""
        growth = 0

        def retain(mapping, key, value):
            nonlocal growth
            old = mapping.get(key)
            growth += max(0, len(canonical_json({key: value}).encode("utf-8")) -
                (len(canonical_json({key: old}).encode("utf-8")) if old is not None else 0)) + 1
            mapping[key] = value

        identities = {tuple(current[(row["file_id"], row["source_row_number"])]["account"]["auto_identity"])
            for row in result["processed_rows"] if row["created_review_id"] and
            current[(row["file_id"], row["source_row_number"])]["account"]["auto_identity"] and
            not current[(row["file_id"], row["source_row_number"])]["account"]["ref_id"]}
        refs = self.mapper.accounts.reliable_refs(identities)
        for identity in identities:
            row = refs.get(identity)
            if (row is None or row["account_id"] or row["status"] != "ACTIVE" or row["identity_strength"] != 1 or
                    any(row[field] for field in ("name", "institution", "reference"))):
                fail("STALE_PREVIEW")
            retain(self.guard["refs"], str(row["id"]), row)
        files = {candidate["premise"]["file"]["id"]: candidate["premise"]["file"] for candidate in current.values()}
        for row in result["processed_rows"]:
            id = str(row["transaction_id"])
            if row["row_status"] != 1 or id not in self.targets:
                continue
            key = row["file_id"], row["source_row_number"]
            candidate, file = current[key], files[key[0]]
            # The source envelope was verified before writing and is immutable.
            # Only the exact actual accepted row ID is certified, never a count.
            proof = dict(row_id=row["row_id"], file_id=key[0], file_sha256=file["sha256"],
                file_format=file["file_format"], source_row_number=key[1], raw_hash=candidate["raw_hash"],
                evidence_hash=fingerprint(candidate["raw_payload"]))
            if id not in self.guard["proofs"]:
                growth += len(id) + 6
            retain(self.guard["proofs"].setdefault(id, {}), str(row["row_id"]), proof)
        if before_rules:
            after = {row["id"]: row for row in self.mapper.named_rows("rules", {row["id"] for row in before_rules})}
            changing = {"scan_epoch", "scan_after_ledger_id", "updated_time"}
            for before in before_rules:
                row = after.get(before["id"])
                if (row is None or any(row[name] != value for name, value in before.items() if name not in changing) or
                        row["scan_epoch"] != before["scan_epoch"] + 1 or
                        not 0 <= row["scan_after_ledger_id"] <= before["scan_after_ledger_id"] or
                        row["updated_time"] == before["updated_time"]):
                    fail("STALE_PREVIEW")
                retain(self.guard["rule_effects"], str(row["id"]), row)
        self.guard["next"] += 1
        return growth + 5
