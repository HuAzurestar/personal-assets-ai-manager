"""Selected-scope source matching and atomic import persistence.

The caller owns BEGIN IMMEDIATE/commit. Source evidence is immutable; only an
explicit recheck may change an unaccepted row's disposition. No result replay.
"""
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
from time import monotonic

from sqlalchemy import and_, case, func, insert, select, tuple_, update
from sqlalchemy.exc import OperationalError

from backend.core.import_identity import (SOURCE_CODES, FORMAT_CODES, canonical_json,
    fact_values, raw_evidence, same_fact, source_account_code)
from backend.core.source_account_identity import reliable_source
from backend.entity import TransactionFact, TransactionImportFile, TransactionImportRow
from backend.entity.base import utc_now
from backend.error import TargetIntakeError
from backend.mapper.account_management_mapper import AccountManagementMapper
from backend.mapper.review_command_mapper import ReviewCommandMapper, chunks
from backend.service.target_tag_projection_service import TargetTagProjectionService


def fail(code, status=409):
    raise TargetIntakeError(status, code, code=code)


def fingerprint(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class ImportBatchMapper(ReviewCommandMapper):
    def __init__(self, db):
        super().__init__(db)
        self.accounts = AccountManagementMapper(db)

    def end_write(self):
        super().end_write()
        self.write_started = None

    def mark_abandoned(self, ids):
        now = utc_now()
        rows = self.rows(TransactionImportFile, TransactionImportFile.id, ids)
        pending = {row["id"]: row for row in rows if row["status"] == 0}
        for batch in chunks(pending):
            updated_time = max(now, max(pending[id]["updated_time"] for id in batch) + timedelta(microseconds=1))
            self.db.execute(update(TransactionImportFile).where(TransactionImportFile.id.in_(batch),
                TransactionImportFile.status == 0).values(status=3, updated_time=updated_time))

    def expired_page(self, cursor, cutoff=None):
        statement = select(TransactionImportFile.id).where(TransactionImportFile.id > cursor,
                                                         TransactionImportFile.status == 0)
        if cutoff is not None:
            statement = statement.where(TransactionImportFile.updated_time < cutoff)
        return list(self.db.scalars(statement.order_by(TransactionImportFile.id).limit(1000)))

    @contextmanager
    def match_budget(self):
        started = monotonic()
        driver = self.db.connection().connection.driver_connection
        driver.set_progress_handler(lambda: int(monotonic() - started > 2 or
            self.write_started is not None and monotonic() - self.write_started > 2), 1000)
        try:
            yield started
            if monotonic() - started > 2:
                fail("IMPORT_MATCH_LIMIT", 422)
        except OperationalError as error:
            if "interrupted" in str(error).lower():
                fail("WRITE_BUSY" if self.write_started is not None else "IMPORT_MATCH_LIMIT",
                     503 if self.write_started is not None else 422)
            raise
        finally:
            if self.write_started is not None:
                self.resume_write_budget()
            else:
                driver.set_progress_handler(None, 0)

    def prepare_files(self, batch_code, uploads):
        """Reuse hash identity without resetting metadata, rows or first batch."""
        by_hash = {item["sha256"]: item for item in uploads}
        existing = {}
        for batch in chunks(by_hash):
            existing.update({row["sha256"]: dict(row) for row in self.db.execute(select(
                TransactionImportFile.__table__).where(TransactionImportFile.sha256.in_(batch))).mappings()})
        now = utc_now()
        new = [TransactionImportFile(batch_code=batch_code, filename=by_hash[key]["filename"], sha256=key,
               status=0, created_time=now, updated_time=now) for key in sorted(set(by_hash) - set(existing))]
        self.db.add_all(new)
        self.db.flush()
        existing.update({row["sha256"]: row for row in self.rows(
            TransactionImportFile, TransactionImportFile.id, [item.id for item in new])})
        return existing

    def persist_parse(self, documents):
        files = {row["id"]: row for row in self.rows(TransactionImportFile, TransactionImportFile.id,
                                                   [doc["file_id"] for doc in documents])}
        updates = []
        now = utc_now()
        for doc in documents:
            old = files[doc["file_id"]]
            if old["sha256"] != doc["sha256"]:
                fail("IDENTITY_CHANGED")
            if doc.get("error"):
                if old["status"] == 0:
                    updates.append(dict(id=old["id"], status=3,
                        updated_time=max(now, old["updated_time"] + timedelta(microseconds=1))))
                continue
            dated = sorted(row["occurred_at"] for row in doc["rows"] if row.get("occurred_at") and not row.get("error"))
            values = dict(source_type=SOURCE_CODES[doc["source_type"]], file_format=FORMAT_CODES[doc["format"]],
                total_count=len(doc["rows"]), period_start=dated[0] if dated else "", period_end=dated[-1] if dated else "")
            # Once parsed, even zero-row identity is fixed by source/format.
            if old["source_type"] != 0 or old["file_format"] != 0:
                if any(old[key] != value for key, value in values.items()):
                    fail("IDENTITY_CHANGED")
                continue
            updates.append(dict(id=old["id"], **values,
                status=1 if not doc["rows"] else 0,
                updated_time=max(now, old["updated_time"] + timedelta(microseconds=1))))
        if updates:
            # Failed documents and successful parses have different update keys.
            for keys in {tuple(sorted(row)) for row in updates}:
                self.db.execute(update(TransactionImportFile), [row for row in updates if tuple(sorted(row)) == keys])

    def source_rows(self, identities):
        identities = sorted(set(identities))
        result = {}
        for offset in range(0, len(identities), 400):
            for row in self.db.execute(select(TransactionImportRow.__table__).where(tuple_(
                TransactionImportRow.transaction_import_file_id, TransactionImportRow.source_row_number
            ).in_(identities[offset:offset + 400]))).mappings():
                result[(row["transaction_import_file_id"], row["source_row_number"])] = dict(row)
        return result

    @staticmethod
    def evidence_projection(evidence):
        payload = evidence.get("raw_payload")
        if payload is None or len(payload.encode("utf-8")) > 1024 * 1024:
            fail("RELATION_BROKEN")
        try:
            envelope = json.loads(payload)
            raw, normalized = envelope["raw"], envelope["normalized"]
            if fingerprint(raw) != evidence["raw_hash"] or not isinstance(normalized, dict):
                fail("RELATION_BROKEN")
            return normalized
        except (ValueError, TypeError, KeyError):
            fail("RELATION_BROKEN")

    @classmethod
    def accepted_projection(cls, fact, evidence):
        """Verify legacy own-source digest from original normalized evidence.

        Never rewrite a legacy fact_key/account_code, and never use a selected
        Ledger account or a masked suffix to project the original source.
        """
        normalized = cls.evidence_projection(evidence)
        account_code = source_account_code(normalized)
        legacy_identity = normalized.get("account", {}).get("identity")
        if fact["account_code"] not in {account_code, legacy_identity}:
            fail("RELATION_BROKEN")
        projected = fact | dict(account_code=account_code)
        try:
            if not same_fact(fact_values(normalized | dict(row_number=evidence["source_row_number"]), ""), projected):
                fail("RELATION_BROKEN")
        except (ValueError, TypeError, KeyError):
            fail("RELATION_BROKEN")
        return projected

    def candidates(self, parsed, files, stored):
        keys = {value["fact_key"] for value in parsed.values()}
        by_key = {}
        for batch in chunks(keys):
            for fact in self.db.execute(select(TransactionFact.__table__).where(
                TransactionFact.fact_key.in_(batch))).mappings():
                by_key[fact["fact_key"]] = dict(fact)
        pointers = {row["transaction_fact_id"] for row in stored.values() if row["transaction_fact_id"] > 0}
        pointed = {row["id"]: row for row in self.rows(TransactionFact, TransactionFact.id, pointers)}
        if pointers != set(pointed):
            fail("RELATION_BROKEN")
        references = {(files[key[0]]["source_type"], value.get("reference", ""))
                      for key, value in self._input_rows.items() if value.get("reference") and key in parsed}
        source_pairs = sorted(references)
        evidence_by_pair = {}
        candidate_facts = {}
        count = 0
        # Indexed source_reference pairs, not whole-history or date-window scans.
        for offset in range(0, len(source_pairs), 400):
            statement = select(TransactionImportRow.__table__, TransactionImportFile.source_type.label("file_source"))\
                .join(TransactionImportFile, TransactionImportFile.id == TransactionImportRow.transaction_import_file_id)\
                .where(TransactionImportRow.source_reference != "", TransactionImportRow.row_status == 1,
                    tuple_(TransactionImportFile.source_type, TransactionImportRow.source_reference)
                    .in_(source_pairs[offset:offset + 400])).order_by(TransactionImportRow.id).limit(50001 - count)
            found = [dict(row) for row in self.db.execute(statement).mappings()]
            count += len(found)
            if count > 50000:
                fail("IMPORT_MATCH_LIMIT", 422)
            for row in found:
                if row["transaction_fact_id"] <= 0:
                    fail("RELATION_BROKEN")
                evidence_by_pair.setdefault((row["file_source"], row["source_reference"]), []).append(row)
                candidate_facts[row["transaction_fact_id"]] = None
        candidate_facts.update({row["id"]: row for row in self.rows(TransactionFact, TransactionFact.id, candidate_facts)})
        if any(value is None for value in candidate_facts.values()):
            fail("RELATION_BROKEN")
        result = {}
        # Project once per evidence, never JSON-decode candidates per input row.
        projected = {}
        for pair, evidence_rows in evidence_by_pair.items():
            for evidence in evidence_rows:
                fact = self.accepted_projection(candidate_facts[evidence["transaction_fact_id"]], evidence)
                if SOURCE_CODES.get(self.evidence_projection(evidence).get("source_type")) != pair[0]:
                    fail("RELATION_BROKEN")
                projected.setdefault(pair + (fact["account_code"],), {})[fact["id"]] = fact
        by_identity_key = {}
        self._candidate_hashes = {}
        for identity, values in parsed.items():
            row = self._input_rows[identity]
            key = values["fact_key"]
            if key not in by_identity_key:
                found = dict(projected.get((files[identity[0]]["source_type"], row.get("reference", ""), values["account_code"]), {}))
                if key in by_key:
                    fact = by_key[key]
                    found[fact["id"]] = fact
                by_identity_key[key] = found
                self._candidate_hashes[key] = fingerprint([found[id] for id in sorted(found)])
            found = by_identity_key[key]
            existing = stored.get(identity)
            if existing and existing["transaction_fact_id"]:
                fact = self.accepted_projection(pointed[existing["transaction_fact_id"]], existing)
                if fact["id"] not in found:
                    found = found | {fact["id"]: fact}
            result[identity] = (found, self._candidate_hashes[key] if found is by_identity_key[key]
                                else fingerprint([found[id] for id in sorted(found)]))
        return result

    def account_premises(self, rows, choices):
        identities = {identity for row in rows.values() if (identity := reliable_source(row)) is not None}
        reliable = self.accounts.reliable_refs(identities)
        requested = {choice["account_ref_id"] for choice in choices.values() if choice.get("account_ref_id")}
        ref_ids = requested | {row["id"] for row in reliable.values()}
        refs = {row["id"]: row for row in self.accounts.named_rows("refs", ref_ids)}
        accounts = {row["id"]: row for row in self.accounts.named_rows("accounts", [ref["account_id"] for ref in refs.values() if ref["account_id"]])}
        parties = {row["id"]: row for row in self.accounts.named_rows("parties", [row["party_id"] for row in accounts.values()])}
        result = {}
        for key, row in rows.items():
            identity = reliable_source(row)
            auto = reliable.get(identity)
            explicit = choices.get(key, {}).get("account_ref_id")
            ref_id = explicit if explicit is not None else auto["id"] if auto else 0
            chain = []
            issue = None
            if ref_id:
                ref = refs.get(ref_id)
                if ref is None:
                    issue = "REFERENCE_NOT_FOUND"
                else:
                    chain.append(ref)
                    if identity is not None and (ref["identity_strength"] != 1 or
                        (ref["source_namespace"], ref["source_identity"]) != identity):
                        issue = "ACCOUNT_BINDING_CONFLICT"
                    if ref["account_id"]:
                        account = accounts.get(ref["account_id"])
                        if account is None or account["party_id"] not in parties:
                            issue = "ACCOUNT_RELATION_BROKEN"
                        else:
                            chain += [account, parties[account["party_id"]]]
                    if any(item["status"] != "ACTIVE" for item in chain):
                        issue = "ACCOUNT_NOT_ACTIVE"
            result[key] = dict(ref_id=ref_id, auto_identity=list(identity) if identity and explicit is None else None,
                               chain=chain, issue=issue)
        return result

    def match(self, input_rows, choices):
        """Snapshot one selected scope, safe for both preview and locked recheck."""
        with self.match_budget():
            return self._match(input_rows, choices)

    def _match(self, input_rows, choices):
        choices = {key: choices[key] for key in input_rows if key in choices}
        self._input_rows = input_rows
        files = {row["id"]: row for row in self.rows(TransactionImportFile, TransactionImportFile.id, [key[0] for key in input_rows])}
        if set(files) != {key[0] for key in input_rows}:
            fail("RELATION_BROKEN")
        stored = self.source_rows(input_rows)
        parsed, errors, raw = {}, {}, {}
        for key, row in input_rows.items():
            if row["row_number"] != key[1] or SOURCE_CODES.get(row["source_type"]) != files[key[0]]["source_type"]:
                fail("IDENTITY_CHANGED")
            raw[key] = raw_evidence(row, row.get("_document", {}))
            try:
                parsed[key] = fact_values(row, files[key[0]]["sha256"])
            except (ValueError, TypeError, KeyError):
                errors[key] = ("NON_POSTED_EVIDENCE" if not row.get("error") and row.get("disposition") == "non_posted"
                    else "NEUTRAL_EVIDENCE" if not row.get("error") and row.get("disposition") == "neutral_evidence" else "ROW_INVALID")
        found = self.candidates(parsed, files, stored)
        account = self.account_premises(input_rows, choices)
        result, groups = {}, {}
        for key in input_rows:
            existing = stored.get(key)
            issue = errors.get(key)
            if existing:
                if existing["row_status"] not in {0, 1, 2, 3} or (existing["row_status"] == 1) != (existing["transaction_fact_id"] > 0):
                    fail("RELATION_BROKEN")
                if existing["raw_hash"] != raw[key][0]:
                    fail("IDENTITY_CHANGED")
                # Preserve original envelope, but require identical parser facts.
                original = self.evidence_projection(existing)
                projected = json.loads(raw[key][1])["normalized"]
                if any(original.get(field) != value for field, value in projected.items()
                       if field != "source_account" or field in original):
                    fail("IDENTITY_CHANGED")
            matches, matches_hash = found.get(key, ({}, fingerprint([])))
            classification = "INVALID" if issue else "EXISTING" if matches else "NEW"
            if len(matches) > 1:
                classification, issue = "AMBIGUOUS", "IDENTITY_AMBIGUOUS"
            elif matches and not same_fact(parsed[key], next(iter(matches.values()))):
                classification, issue = "INVALID", "FACT_CONFLICT"
            if existing and existing["row_status"] == 1:
                classification = "PROCESSED"
            fact_id = next(iter(matches)) if len(matches) == 1 and not issue else 0
            # Existing Facts are evidence-only. A new account intent cannot move them.
            if fact_id and (not existing or existing["row_status"] != 1) and choices.get(key, {}).get("account_ref_id") is not None:
                fail("EXISTING_ACCOUNT_READ_ONLY", 422)
            if not fact_id and not issue and account[key]["issue"]:
                classification, issue = "INVALID", account[key]["issue"]
            if input_rows[key].get("_group_issue") and classification != "PROCESSED":
                classification, issue = "INVALID", input_rows[key]["_group_issue"]
            stored_premise = {field: value for field, value in existing.items() if field != "raw_payload"} if existing else None
            if stored_premise is not None:
                stored_premise["evidence_hash"] = fingerprint(existing["raw_payload"])
            result[key] = dict(classification=classification, issue=issue, values=parsed.get(key), fact_id=fact_id,
                stored={field: value for field, value in existing.items() if field != "raw_payload"} if existing else None,
                raw_hash=raw[key][0], raw_payload=raw[key][1], account=account[key],
                premise=dict(file={field: files[key[0]][field] for field in ("id", "sha256", "source_type", "file_format", "total_count")},
                             stored=stored_premise, matches_hash=matches_hash, account=account[key]))
            if key in parsed:
                groups.setdefault(parsed[key]["fact_key"], []).append(key)
        for group in groups.values():
            core = result[group[0]]["values"]
            conflict = any(not same_fact(core, result[key]["values"]) for key in group[1:])
            selected_refs = {(item["ref_id"], tuple(item["auto_identity"] or []))
                             for key in group if choices.get(key, {}).get("decision") == "ACCEPT"
                             for item in [account[key]]}
            for key in group:
                if conflict or len(selected_refs) > 1:
                    result[key].update(classification="INVALID", issue="FACT_CONFLICT" if conflict else "ACCOUNT_BINDING_CONFLICT")
        for key, candidate in result.items():
            candidate["premise_hash"] = fingerprint(candidate["premise"] | dict(
                classification=candidate["classification"], issue=candidate["issue"], values=candidate["values"],
                choice=choices.get(key), parser=input_rows[key].get("_document", {}),
                group=input_rows[key].get("_group_token")))
        return result

    @staticmethod
    def validate_selection(candidates, choices, *, processed_only=False):
        for key, candidate in candidates.items():
            choice, stored = choices.get(key), candidate["stored"]
            if choice is None:
                fail("ROW_CHOICE_REQUIRED", 422)
            if stored and stored["row_status"] == 1:
                fail("ROWS_ALREADY_PROCESSED")
            if stored and stored["row_status"] in {2, 3}:
                if not choice.get("recheck"):
                    fail("ROW_RECHECK_REQUIRED")
                if choice["decision"] != "ACCEPT":
                    fail("ROWS_ALREADY_PROCESSED")
            if not processed_only and choice["decision"] == "ACCEPT" and candidate["issue"]:
                code = candidate["issue"]
                fail("ROW_INVALID" if code in {"NON_POSTED_EVIDENCE", "NEUTRAL_EVIDENCE"} else code,
                     409 if code.startswith("ACCOUNT_") else 422)

    def write_batch(self, candidates, choices, order, *, fault=None):
        self.validate_selection(candidates, choices)
        now = utc_now()
        new_by_key = {}
        for key, candidate in candidates.items():
            if choices[key]["decision"] == "ACCEPT" and not candidate["fact_id"]:
                new_by_key.setdefault(candidate["values"]["fact_key"], (candidate["values"], candidate["account"]))
        new = [TransactionFact(**values, created_time=now, updated_time=now) for values, _account in new_by_key.values()]
        self.db.add_all(new)
        self.db.flush()
        facts = {fact.fact_key: fact.id for fact in new}
        reliable = self.accounts.create_reliable_refs({tuple(account["auto_identity"]) for _values, account in new_by_key.values()
                                                      if account["auto_identity"] is not None})
        refs = {facts[key]: reliable[tuple(account["auto_identity"])]["id"] if account["auto_identity"] else account["ref_id"]
                for key, (_values, account) in new_by_key.items()}
        if fault:
            fault("facts")
        reviews, _positions, ledgers, _legs = self.create_initial_defaults(list(facts.values()), account_refs=refs)
        defaults = {fact_id: (review.id, ledger[0].id) for fact_id, review, ledger in zip(sorted(facts.values()), reviews, ledgers)}
        if fault:
            fault("defaults")
        tag_service = TargetTagProjectionService(self.db)
        ledger_ids = [ledger.id for group in ledgers for ledger in group]
        active_views = {item.view_id for item in tag_service.mapper.active_dictionary()} if ledger_ids else set()
        if len(ledger_ids) * len(active_views) > 50000:
            fail("TAG_IMPACT_LIMIT", 413)
        for batch in chunks(ledger_ids):
            tag_service.sync_ledgers(batch)
        if fault:
            fault("tags")
        new_rows, updates, outcomes = [], [], {}
        for key in order:
            candidate, choice = candidates[key], choices[key]
            accepted = choice["decision"] == "ACCEPT"
            status = 1 if accepted else 3 if candidate["issue"] and candidate["issue"] not in {"NON_POSTED_EVIDENCE", "NEUTRAL_EVIDENCE"} else 2
            fact_id = (candidate["fact_id"] or facts[candidate["values"]["fact_key"]]) if accepted else 0
            values = dict(transaction_fact_id=fact_id, row_status=status,
                          issue_code=candidate["issue"] if status == 3 else "", issue_message=candidate["issue"] if status == 3 else "")
            old = candidate["stored"]
            if old:
                updates.append(dict(id=old["id"], **values, updated_time=max(now, old["updated_time"] + timedelta(microseconds=1))))
            else:
                new_rows.append(TransactionImportRow(**values, transaction_import_file_id=key[0], source_row_number=key[1],
                    source_reference=self._input_rows[key].get("reference", ""), raw_hash=candidate["raw_hash"],
                    raw_payload=candidate["raw_payload"], created_time=now, updated_time=now))
            outcomes[key] = dict(file_id=key[0], source_row_number=key[1], row_status=status, transaction_id=fact_id,
                created_review_id=defaults.get(fact_id, (0, 0))[0], created_ledger_id=defaults.get(fact_id, (0, 0))[1])
        self.db.add_all(new_rows)
        self.db.flush()
        if updates:
            self.db.execute(update(TransactionImportRow), updates)
        if fault:
            fault("source_rows")
        stored = self.source_rows(order)
        for key, result in outcomes.items():
            result["row_id"] = stored[key]["id"]
        files = self.progress([key[0] for key in order], persist=True)
        if fault:
            fault("file_counts")
        return dict(files=files, processed_rows=[outcomes[key] for key in order], new_fact_count=len(new),
            linked_existing_count=sum(choices[key]["decision"] == "ACCEPT" and bool(candidates[key]["fact_id"]) for key in order),
            skipped_count=sum(item["row_status"] == 2 for item in outcomes.values()),
            invalid_count=sum(item["row_status"] == 3 for item in outcomes.values()),
            remaining_count=sum(file["remaining"] for file in files))

    def progress(self, file_ids, *, persist=False):
        files = self.rows(TransactionImportFile, TransactionImportFile.id, file_ids)
        counts = {}
        for batch in chunks(file_ids):
            for row in self.db.execute(select(TransactionImportRow.transaction_import_file_id,
                *[func.sum(case((TransactionImportRow.row_status == status, 1), else_=0)).label(name)
                  for status, name in ((1, "accepted"), (2, "skipped"), (3, "invalid"))])
                .where(TransactionImportRow.transaction_import_file_id.in_(batch))
                .group_by(TransactionImportRow.transaction_import_file_id)).mappings():
                counts[row["transaction_import_file_id"]] = dict(row)
        result, updates = [], []
        now = utc_now()
        for file in files:
            current = counts.get(file["id"], {})
            accepted, skipped, invalid = [current.get(key, 0) for key in ("accepted", "skipped", "invalid")]
            n = accepted + skipped + invalid
            remaining = file["total_count"] - n
            if remaining < 0:
                fail("DATA_INTEGRITY_ERROR")
            status = (2 if n else 0) if remaining else (1 if not invalid else 2 if accepted + skipped else 3)
            updated_time = file["updated_time"]
            if persist:
                updated_time = max(now, updated_time + timedelta(microseconds=1))
                updates.append(dict(id=file["id"], success_count=accepted, skip_count=skipped, issue_count=invalid,
                    status=status, updated_time=updated_time))
            result.append(dict(file_id=file["id"], sha256=file["sha256"], status=status if persist else file["status"],
                updated_time=updated_time, accepted=accepted, skipped=skipped, invalid=invalid, remaining=remaining))
        if updates:
            self.db.execute(update(TransactionImportFile), updates)
        return result
