"""Bounded v1 import use case, prepared for the single runtime cutover.

No receipt store, whole-plan confirmation or implicit unaccepted-row choices.
"""
import base64
from collections import Counter, defaultdict
import hashlib
from pathlib import PureWindowsPath
from time import monotonic
from uuid import uuid4
from datetime import timedelta

from sqlalchemy.exc import IntegrityError, OperationalError

from backend.core.import_identity import fact_key, fact_values, same_fact, canonical_json
from backend.core.import_public_text import masked_summary
from backend.core.import_preview_store import ImportPreviewState, import_preview_store
from backend.core.config import IMPORT_PREVIEW_TIMEOUT_MINUTES
from backend.core.feature_observability import observed, observability
from backend.entity.base import utc_now
from backend.core.source_account_identity import reliable_source
from backend.error import ListQueryError, TargetIntakeError
from backend.error.statement_parse import PARSE_ISSUES, public_parse_code
from backend.entity import TransactionImportFile
from backend.mapper.import_batch_mapper import ImportBatchMapper, fail, fingerprint
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.mapper.bounded_query_mapper import query_budget
from backend.parser.bounded_statement import parse_statement
from backend.schema.list_query import FilterFieldExpression
from backend.service.import_confirmation_service import ImportConfirmationService
from backend.service.import_duplicate_service import ImportDuplicateService
from backend.service.import_match_service import ImportMatchService
from backend.service.import_risk_service import ImportRiskService
from backend.service.import_operation_service import ImportOperationService
from backend.core.import_evidence import target_locator


class ImportBatchService:
    sweep_cursor = 0
    def __init__(self, db, *, store=None):
        self.db = db
        self.mapper = ImportBatchMapper(db)
        self.store = store if store is not None else import_preview_store

    def write_metadata(self, action):
        try:
            self.mapper.begin_write()
            result = action()
            if monotonic() - self.mapper.write_started > 2:
                fail("WRITE_BUSY", 503)
            self.db.commit()
            return result
        except (OperationalError, IntegrityError):
            self.db.rollback()
            fail("WRITE_BUSY", 503)
        except Exception:
            self.db.rollback()
            raise
        finally:
            self.mapper.end_write()

    @staticmethod
    def group_premises(state):
        """In-memory O(R) immutable-source groups; no whole-file DB recheck."""
        groups = defaultdict(list)
        file_hashes = {file["file_id"]: file["sha256"] for file in state.files}
        for key, row in state.rows.items():
            row.pop("_group_issue", None)
            row.pop("_group_token", None)
            try:
                values = fact_values(row, file_hashes[key[0]])
            except (ValueError, KeyError, TypeError):
                continue
            groups[fact_key(row, file_hashes[key[0]])].append((key, values))
        for group in groups.values():
            refs = set()
            for key, _values in group:
                choice = state.choices.get(key, {})
                if choice.get("decision") == "ACCEPT":
                    # Auto source identity is stable even before its ref exists.
                    explicit = choice.get("account_ref_id")
                    refs.add(("EXPLICIT", explicit) if explicit is not None else
                             ("AUTO", reliable_source(state.rows[key])))
            issue = "FACT_CONFLICT" if any(not same_fact(group[0][1], values) for _key, values in group[1:]) else \
                    "ACCOUNT_BINDING_CONFLICT" if len(refs) > 1 else None
            token = fingerprint([dict(identity=list(key), values=values, choice=state.choices.get(key))
                                 for key, values in sorted(group)])
            for key, _values in group:
                state.rows[key]["_group_token"] = token
                if issue:
                    state.rows[key]["_group_issue"] = issue

    def preview(self, payload, *, source_timezone):
        # Decode/hash and parse outside SQLite's writer lock. Never retain bytes
        # or passwords in the process-local preview.
        if sum(len(item.content_base64) for item in payload.files) > 27962428:
            fail("INPUT_LIMIT", 413)
        decoded, total = [], 0
        for item in payload.files:
            try:
                content = base64.b64decode(item.content_base64, validate=True)
            except (ValueError, TypeError):
                fail("INVALID_BASE64", 422)
            total += len(content)
            if not content or total > 20 * 1024 * 1024:
                fail("INPUT_LIMIT", 413)
            decoded.append((item, content, hashlib.sha256(content).hexdigest()))
        token = uuid4().hex
        files = self.write_metadata(lambda: self.mapper.prepare_files(token, [
            dict(filename=PureWindowsPath(item.filename).name, sha256=sha) for item, _content, sha in decoded]))
        documents = {}
        started, row_count = monotonic(), 0
        for item, content, sha in decoded:
            if sha in documents:
                continue
            if monotonic() - started >= 30:
                fail("PARSE_LIMIT", 422)
            try:
                doc = parse_statement(content, item.filename, item.password, item.source_type,
                                      source_timezone=source_timezone, deadline=started + 30)
            except (ValueError, TypeError) as error:
                # Raw parser exceptions may contain account numbers or content.
                doc = dict(filename=PureWindowsPath(item.filename).name, sha256=sha, rows=[], error=public_parse_code(error))
            doc["file_id"] = files[sha]["id"]
            documents[sha] = doc
            row_count += len(doc["rows"])
            observability.metric("import_rows", "IMPORT_PARSE", len(doc["rows"]))
            if row_count > 20000 or monotonic() - started > 30:
                fail("PARSE_LIMIT", 422)
        self.write_metadata(lambda: self.mapper.persist_parse(list(documents.values())))
        summaries, rows = [], {}
        for sha, doc in documents.items():
            summaries.append(dict(file_id=doc["file_id"], sha256=sha, filename=PureWindowsPath(doc["filename"]).name,
                parsed_row_count=len(doc["rows"]), error=doc.get("error")))
            metadata = {key: doc.get(key) for key in ("parser_version", "source_timezone")}
            for row in doc["rows"]:
                # Source/account provenance belongs to the parser document even
                # when a row failed amount/date parsing; never invent Fact data.
                row = dict(row)
                row.setdefault("source_type", doc["source_type"])
                row.setdefault("account", doc.get("account", {}))
                row.setdefault("profile", doc.get("profile", ""))
                row["_document"] = metadata
                rows[(doc["file_id"], row["row_number"])] = row
        state = ImportPreviewState(token=token, files=summaries, rows=rows)
        self.group_premises(state)
        state.candidates = self.mapper.match(state.rows, {})
        self.db.rollback()
        removed = self.store.put(state)
        self.release_files(removed)
        return self.current(token)

    @staticmethod
    def digest(state):
        # Advisory file status/time and TTL are deliberately excluded.
        return fingerprint(dict(files=state.files,
            candidates=[dict(identity=list(key), premise=candidate["premise_hash"], choice=state.choices.get(key))
                        for key, candidate in sorted(state.candidates.items())]))

    def current(self, token):
        with query_budget(self.db):
            TrustedRelationMapper(self.db).read_snapshot()
            return self._current(token)

    def _current(self, token):
        state = self.store.get(token)
        progress = {row["file_id"]: row for row in self.mapper.progress([file["file_id"] for file in state.files])}
        stored_files = {row["id"]: row for row in self.mapper.rows(TransactionImportFile, TransactionImportFile.id,
                                                                 [file["file_id"] for file in state.files])}
        files = []
        for file in state.files:
            item = {key: value for key, value in file.items() if key != "error"}
            code = file.get("error") or ""
            item.update(parse_status="FAILED" if code else "READY" if file["parsed_row_count"] else "EMPTY",
                parse_issue_code=code, parse_issue_message=PARSE_ISSUES.get(code, ("", ""))[0],
                parse_recovery=PARSE_ISSUES.get(code, ("", ""))[1])
            item.update({key: progress[file["file_id"]][key] for key in ("accepted", "skipped", "invalid", "remaining")})
            # Genuine parsed activity extrema, not asserted statement coverage.
            info = stored_files[file["file_id"]]
            item["activity_range"] = dict(start=info["period_start"] or None, end=info["period_end"] or None)
            files.append(item)
        counts = Counter(candidate["classification"].lower() for candidate in state.candidates.values())
        issues = [dict(file_id=key[0], source_row_number=key[1], code=candidate["issue"])
                  for key, candidate in sorted(state.candidates.items()) if candidate["issue"]]
        issues += [dict(file_id=file["file_id"], source_row_number=0, code=file["error"])
                   for file in state.files if file.get("error")]
        return dict(token=token, updated_time=state.updated_time, status=state.status, timed_out=state.timed_out,
            files=files, counts={key: counts[key] for key in ("new", "existing", "processed", "invalid", "ambiguous")},
            preview_digest=self.digest(state), issues=issues[:100], issue_count=len(issues), has_more_issues=len(issues) > 100)

    def revise(self, token, payload):
        state = self.store.get(token)
        if state.updated_time != payload.expected_updated_time:
            fail("PREVIEW_CHANGED")
        changed = set()
        for choice in payload.choices:
            key = (choice.file_id, choice.source_row_number)
            if key not in state.rows:
                fail("PREVIEW_ROW_NOT_FOUND", 404)
            state.choices[key] = choice.model_dump()
            changed.add(key)
        old_groups = {key: row.get("_group_token") for key, row in state.rows.items()}
        self.group_premises(state)
        changed |= {key for key, row in state.rows.items() if old_groups[key] != row.get("_group_token")}
        # A ROW pair's other endpoint can live on another page or have been
        # saved by an earlier PUT. Include both endpoints and dependent pairs
        # in this read-only recheck, not in an implicit financial selection.
        dependencies = defaultdict(set)
        for key, choice in state.choices.items():
            if choice.get("target") and choice["target"]["kind"] == "ROW":
                _kind, target = target_locator(choice["target"])
                dependencies[key].add(target)
                dependencies[target].add(key)
        pending = list(changed)
        while pending:
            for neighbor in dependencies[pending.pop()]:
                if neighbor not in state.rows:
                    fail("INVALID_EVIDENCE_TARGET", 422)
                if neighbor not in changed:
                    changed.add(neighbor)
                    pending.append(neighbor)
        candidates = self.mapper.match({key: state.rows[key] for key in changed}, state.choices)
        # Rechecking a persisted non-accepted row requires explicit authorization
        # even before confirmation. SKIP on a new invalid row remains legal.
        for choice in payload.choices:
            key = (choice.file_id, choice.source_row_number)
            stored = candidates[key]["stored"]
            if stored and stored["row_status"] == 1:
                fail("ROWS_ALREADY_PROCESSED")
            if stored and stored["row_status"] in {2, 3} and not choice.recheck:
                fail("ROW_RECHECK_REQUIRED")
        state.candidates.update(candidates)
        self.db.rollback()
        removed = self.store.replace(state, payload.expected_updated_time)
        self.release_files(removed)
        return self.current(token)

    def confirm_preview(self, token, payload):
        state = self.store.get(token)
        if state.status == "CONFIRMING":
            fail("PREVIEW_BUSY")
        if state.updated_time != payload.expected_updated_time or self.digest(state) != payload.preview_digest:
            fail("STALE_PREVIEW")
        order = [(row.file_id, row.source_row_number) for row in payload.selected_rows]
        if any(key not in state.rows for key in order):
            fail("PREVIEW_ROW_NOT_FOUND", 404)
        choices = {key: state.choices[key] for key in order if key in state.choices}
        rows = {key: state.rows[key] for key in order}
        try:
            current = self.mapper.match(rows, choices)
            if any(current[key]["premise_hash"] != state.candidates[key]["premise_hash"] for key in order):
                fail("STALE_PREVIEW")
            with query_budget(self.db):
                result = ImportConfirmationService(self.mapper).plan(state, order, current, payload.preview_digest)
            latest = self.store.get(token)
            if latest.updated_time != payload.expected_updated_time or latest.status == "CONFIRMING" or self.digest(latest) != payload.preview_digest:
                fail("STALE_PREVIEW")
            return result
        finally:
            self.db.rollback()

    def operation_preview(self, token, payload):
        """Disclose the complete operation; no financial write or cache queue."""
        state = self.store.get(token)
        if state.status == "CONFIRMING":
            fail("PREVIEW_BUSY")
        if state.updated_time != payload.expected_updated_time or self.digest(state) != payload.preview_digest:
            fail("STALE_PREVIEW")
        order = sorted((row.file_id, row.source_row_number) for row in payload.selected_rows)
        if any(key not in state.rows for key in order):
            fail("PREVIEW_ROW_NOT_FOUND", 404)
        rows = {key: state.rows[key] for key in order}
        choices = {key: state.choices[key] for key in order if key in state.choices}
        started = monotonic()
        try:
            candidates = self.mapper.operation_match(rows, choices)
            if any(candidates[key]["premise_hash"] != state.candidates[key]["premise_hash"] for key in order):
                fail("STALE_PREVIEW")
            risks = ImportRiskService(self.mapper).plan(state.rows, state.candidates | candidates, order)
            remaining = 30 - (monotonic() - started)
            if remaining <= 0:
                fail("QUERY_BUSY", 503)
            with query_budget(self.db, seconds=remaining):
                result = ImportOperationService(self.mapper, deadline=started + 30).plan(state, order, candidates, risks, payload.preview_digest)
            latest = self.store.get(token)
            if latest.updated_time != payload.expected_updated_time or latest.status == "CONFIRMING" or self.digest(latest) != payload.preview_digest:
                fail("STALE_PREVIEW")
            return result
        finally:
            self.db.rollback()

    @observed("IMPORT_BATCH")
    def confirm(self, token, payload, *, fault=None):
        with self.store.claim(token, payload.expected_updated_time) as lease:
            state = lease.state
            if self.digest(state) != payload.preview_digest:
                fail("STALE_PREVIEW")
            order = [(row.file_id, row.source_row_number) for row in payload.selected_rows]
            if any(key not in state.rows for key in order):
                fail("PREVIEW_ROW_NOT_FOUND", 404)
            rows = {key: state.rows[key] for key in order}
            choices = {key: state.choices[key] for key in order if key in state.choices}
            advanced = any(choice.get("resolution", "AUTO") != "AUTO" or choice.get("acknowledge_new_risk")
                           for choice in choices.values())
            if advanced and payload.batch_preview_digest is None:
                fail("IMPORT_REVIEW_REQUIRED", 422)
            committed = False
            commit_attempted = False
            began = False
            try:
                self.mapper.begin_write()
                began = True
                current = self.mapper.match(rows, choices)
                # Processed-row errors have a distinct action from stale inputs.
                self.mapper.validate_selection(current, choices, processed_only=True)
                if any(current[key]["premise_hash"] != state.candidates[key]["premise_hash"] for key in order):
                    fail("STALE_PREVIEW")
                self.mapper.validate_selection(current, choices)
                if payload.batch_preview_digest is not None:
                    batch = ImportConfirmationService(self.mapper).plan(state, order, current, payload.preview_digest)
                    if batch["batch_preview_digest"] != payload.batch_preview_digest or not batch["can_confirm"]:
                        fail("STALE_PREVIEW")
                else:
                    # Legacy AUTO requests may still process a scoped safe new
                    # row, but cannot bypass current suspected/unknown risk.
                    risks = ImportRiskService(self.mapper).plan(state.rows, state.candidates | current, order)
                    for key in order:
                        ImportRiskService.validate_new_cash(current[key], choices[key], risks[key])
                duplicate = any(candidate.get("duplicate_plan") for candidate in current.values())
                duplicate_service = ImportDuplicateService(self.mapper) if duplicate else None
                before_context = duplicate_service.confirmation.target_context(
                    duplicate_service.existing_ids(current, choices)) if duplicate else None
                result = self.mapper.write_batch(current, choices, order, fault=fault, defer_duplicate=duplicate)
                if duplicate:
                    duplicate_service.publish(current, choices, order, result, batch, before_context, fault=fault)
                if monotonic() - self.mapper.write_started > 2:
                    fail("WRITE_BUSY", 503)
                if fault:
                    fault("before_commit")
                commit_attempted = True
                self.db.commit()
                committed = True
                self.mapper.end_write()
                if fault:
                    fault("after_commit")
                state.candidates.update(self.mapper.match(rows, choices))
                self.db.rollback()
                lease.publish()
                result["preview_updated_time"] = state.updated_time
                return result
            except Exception as error:
                self.db.rollback()
                if committed or commit_attempted:
                    observability.metric("import_result_unknown_count", "IMPORT_BATCH")
                    fail("RESULT_UNKNOWN", 503)
                if began:
                    observability.metric("import_batch_rollback_count", "IMPORT_BATCH")
                if isinstance(error, (OperationalError, IntegrityError)):
                    fail("WRITE_BUSY", 503)
                raise
            finally:
                self.mapper.end_write()

    def release_files(self, removed):
        # Only cancel/eviction advisory PENDING, never discard accepted evidence.
        active = self.store.active_file_ids()
        ids = {file["file_id"] for state in removed if state is not None for file in state.files} - active
        if ids:
            self.write_metadata(lambda: self.mapper.mark_abandoned(ids))

    def cancel(self, token):
        state = self.store.remove(token)
        self.release_files([state])
        return dict(cancelled=state is not None)

    @observed("PREVIEW_SWEEP")
    def sweep_pending(self, *, startup=False, resume=False):
        """Short PK batches; advance only committed cursor, never reset PARTIAL."""
        started, count = monotonic(), 0
        cursor = 0 if startup and not resume else type(self).sweep_cursor
        cutoff = None if startup else utc_now() - timedelta(minutes=IMPORT_PREVIEW_TIMEOUT_MINUTES)
        while monotonic() - started < 2:
            def tick():
                ids = self.mapper.expired_page(cursor, cutoff)
                if monotonic() - started >= 2:
                    fail("WRITE_BUSY", 503)
                self.mapper.mark_abandoned(ids)
                return ids
            try:
                ids = self.write_metadata(tick)
            except Exception:
                observability.metric("preview_sweep_failed_count", "PREVIEW_SWEEP")
                raise
            count += len(ids)
            if len(ids) < 1000:
                type(self).sweep_cursor = 0
                return count
            cursor = ids[-1]
            type(self).sweep_cursor = cursor
        return count

    def fail_expired_pending_files(self):
        return self.sweep_pending()

    def fail_orphaned_pending_files(self):
        # Lifespan calls before writes open. Each pass is bounded; after the
        # initial pass keep its committed cursor rather than restarting at zero.
        count = self.sweep_pending(startup=True)
        while type(self).sweep_cursor:
            count += self.sweep_pending(startup=True, resume=True)
        return count

    @staticmethod
    def row_po(key, candidate, choice, hint):
        values = candidate["values"]
        return dict(file_id=key[0], source_row_number=key[1], classification=candidate["classification"],
            parsed=dict(occurred_time=values["occurred_time"] if values else None,
                        amount=values["amount"] if values else None,
                        currency_code=values["currency_code"] if values else None,
                        cash_direction=("IN" if values["cash_direction"] == 1 else "OUT") if values else None,
                        summary=masked_summary(values["summary"]) if values else ""),
            existing_transaction_id=candidate["fact_id"],
            persisted_row_status=candidate["stored"]["row_status"] if candidate["stored"] else None,
            choice=choice, issue_codes=[candidate["issue"]] if candidate["issue"] else [],
            duplicate_hint=hint,
            account_candidates=[dict(account_ref_id=candidate["account"]["ref_id"], label_masked="已核验来源账户")]
                if candidate["account"]["ref_id"] else [])

    def row_page(self, token, digest, request):
        state = self.store.get(token)
        if digest != self.digest(state):
            fail("PREVIEW_CHANGED")
        if request.query:
            raise ListQueryError("preview rows do not support Query", code="LIST_QUERY_NOT_SUPPORTED")
        # Validate the whole expression once, including on an empty preview or
        # an empty earlier AND branch. Never let data-dependent short circuiting
        # accept an unsupported field, malformed range or unhashable value.
        def compile_filter(expression):
            if expression is None:
                return []
            if isinstance(expression, FilterFieldExpression):
                value = expression.val
                if expression.key == "source_row_number" and expression.op == "between":
                    if not isinstance(value, dict) or set(value) != {"start", "end"} or \
                       type(value["start"]) is not int or type(value["end"]) is not int or \
                       not 0 < value["start"] < value["end"]:
                        raise ListQueryError("invalid preview source-row interval", code="LIST_FILTER_INVALID")
                    return [(expression.key, value["start"], value["end"])]
                if expression.key not in {"file_id", "classification"} or expression.op != "=":
                    raise ListQueryError("unsupported preview filter", code="LIST_FILTER_INVALID")
                if expression.key == "file_id" and (type(value) is not int or value <= 0) or \
                   expression.key == "classification" and (not isinstance(value, str) or value not in {"NEW", "EXISTING", "PROCESSED", "INVALID", "AMBIGUOUS"}):
                    raise ListQueryError("invalid preview filter value", code="LIST_FILTER_INVALID")
                return [(expression.key, value, None)]
            if expression.op != "AND":
                raise ListQueryError("preview filter is AND-only", code="LIST_FILTER_INVALID")
            return [term for child in expression.expression for term in compile_filter(child)]
        terms = compile_filter(request.filter)
        def predicate(key, candidate):
            return all(start <= key[1] < end if field == "source_row_number" else
                       (key[0] if field == "file_id" else candidate["classification"]) == start
                       for field, start, end in terms)
        sorts = [(sort.key, sort.direction) for sort in request.sorter] or [("file_id", "asc"), ("source_row_number", "asc")]
        if len(set(key for key, _direction in sorts)) != len(sorts) or any(key not in {"file_id", "source_row_number"} for key, _direction in sorts):
            raise ListQueryError("invalid preview sort", code="LIST_SORTER_INVALID")
        items = [(key, candidate) for key, candidate in sorted(state.candidates.items()) if predicate(key, candidate)]
        for field, direction in reversed(sorts):
            items.sort(key=lambda item: item[0][0 if field == "file_id" else 1], reverse=direction == "desc")
        start = (request.page_index - 1) * request.page_size
        selected = items[start:start + request.page_size]
        expected_updated_time = state.updated_time
        try:
            risks = ImportRiskService(self.mapper).plan(state.rows, state.candidates, [key for key, _candidate in selected]) if selected else {}
            latest = self.store.get(token)
            if latest.updated_time != expected_updated_time or self.digest(latest) != digest:
                fail("PREVIEW_CHANGED")
            return dict(items=[self.row_po(key, candidate, state.choices.get(key), risks[key]["hint"]) for key, candidate in selected],
                        total=len(items), page_index=request.page_index, page_size=request.page_size)
        finally:
            self.db.rollback()

    def match_page(self, token, digest, key, kind, request):
        state = self.store.get(token)
        if digest != self.digest(state):
            fail("PREVIEW_CHANGED")
        expected_updated_time = state.updated_time
        try:
            result = ImportMatchService(self.db).page(state, key, kind, request)
            latest = self.store.get(token)
            if latest.updated_time != expected_updated_time or self.digest(latest) != digest:
                fail("PREVIEW_CHANGED")
            return result
        finally:
            self.db.rollback()

    def binding_preview(self, token, payload):
        """Read the whole proposed binding, never publish a draft or financial data.

        Reuse canonical matching and full account-chain checks in one snapshot.
        Financial pairing is deliberately not executed/approved here. Its
        original intent and risk consent survive unchanged for later preview.
        """
        state = self.store.get(token)
        if state.status == "CONFIRMING":
            fail("PREVIEW_BUSY")
        if state.updated_time != payload.expected_updated_time or self.digest(state) != payload.preview_digest:
            fail("PREVIEW_CHANGED")
        choices = {(choice.file_id,choice.source_row_number): choice.model_dump() for choice in payload.choices}
        if any(key not in state.rows for key in choices):
            fail("PREVIEW_ROW_NOT_FOUND", 404)
        started = monotonic()
        try:
            # No manual account overrides or LINK/DUP targets in this canonical
            # read: those must produce per-row binding exceptions, not a whole
            # request failure or an alternative financial publication engine.
            rows = {key:{field:value for field,value in state.rows[key].items()
                if field not in {"_group_issue", "_group_token"}} for key in choices}
            basic = {key:{field:choice[field] for field in ("decision", "recheck")} for key,choice in choices.items()}
            current = self.mapper.operation_match(rows,basic)
            issues = {}
            for key,candidate in current.items():
                stored,choice = candidate["stored"],choices[key]
                if stored and stored["row_status"] == 1:
                    issues[key] = "ROWS_ALREADY_PROCESSED"
                elif choice["resolution"] == "LINK_EXISTING" or candidate["fact_id"]:
                    issues[key] = "EXISTING_ACCOUNT_READ_ONLY"
                elif stored and stored["row_status"] in {2,3} and not choice["recheck"]:
                    issues[key] = "ROW_RECHECK_REQUIRED"
                elif candidate["issue"] and candidate["issue"] != candidate["account"]["issue"]:
                    issues[key] = candidate["issue"]
            eligible = {key:row for key,row in rows.items() if key not in issues}
            proposed = {key:choices[key] | dict(account_ref_id=payload.account_ref_id) for key in eligible}
            remaining = 30 - (monotonic() - started)
            if remaining <= 0:
                fail("QUERY_BUSY", 503)
            # The matcher owns its existing 2s/50k guard. Reinstall the outer
            # remaining read budget for account lookup and in-memory projection.
            with query_budget(self.db,seconds=remaining):
                accounts = self.mapper.account_premises(eligible,proposed)
                issues.update({key:value["issue"] for key,value in accounts.items() if value["issue"]})
                state.choices.update(choices)
                state.choices.update({key:choice for key,choice in proposed.items() if key not in issues})
                self.group_premises(state)
                issues.update({key:state.rows[key]["_group_issue"] for key in eligible
                    if key not in issues and state.rows[key].get("_group_issue")})
                result = dict(source_preview_digest=payload.preview_digest,expected_updated_time=payload.expected_updated_time,
                    account_ref_id=payload.account_ref_id,selected_count=len(choices),items=[dict(
                        row=dict(file_id=key[0],source_row_number=key[1]),
                        source_state="RELIABLE" if reliable_source(rows[key]) else "UNKNOWN",
                        applicable=key not in issues,reason_codes=[issues[key]] if key in issues else []) for key in sorted(choices)])
                if len(canonical_json(result).encode("utf-8")) > 24 * 1024 * 1024:
                    fail("DETAIL_LIMIT", 413)
                latest = self.store.get(token)
                if latest.updated_time != payload.expected_updated_time or latest.status == "CONFIRMING" or self.digest(latest) != payload.preview_digest:
                    fail("PREVIEW_CHANGED")
            return result
        finally:
            self.db.rollback()
