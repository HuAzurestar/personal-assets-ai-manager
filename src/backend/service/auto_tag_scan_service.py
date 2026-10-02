"""Bounded automatic-tag scans with safe, correlated diagnostic outcomes."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Protocol

from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.core.job_scheduler import JobCallback, JobRunContext
from backend.core.short_database_work import short_database_work
from backend.core.feature_observability import observed, observability
from backend.error import LlmAdapterError, TargetTagError
from backend.mapper.auto_tag_scan_mapper import ScanPage
from backend.mapper.auto_tag_scan_batch_mapper import AutoTagScanBatchMapper as AutoTagScanMapper
from backend.mapper.bounded_query_mapper import query_budget
from backend.schema.auto_tag_scan import SyntheticTagScanFixture
from backend.schema.llm_analysis import (
    LlmAnalysisInput,
    LlmAnalysisResult,
    LlmCandidate,
    SyntheticLlmAnalysisInput,
)
from backend.service.llm_privacy_service import LlmPrivacyService
from backend.service.llm_prompt_audit_service import PromptAuditContext


class TagAnalyzer(Protocol):
    async def analyze(
        self, payload: LlmAnalysisInput, *, rule_id: int, model_id: int,
        audit_context: PromptAuditContext,
    ) -> LlmAnalysisResult: ...


@dataclass(frozen=True, slots=True)
class ScanRunReport:
    rule_id: int
    inspected_count: int
    submitted_count: int
    request_count: int
    failed_count: int
    stopped_reason: str
    input_failed_count: int = 0
    no_call_count: int = 0
    insufficient_count: int = 0
    skipped_count: int = 0
    successful_count: int = 0
    last_error_code: str | None = None


@dataclass
class _Counts:
    inspected_count: int = 0
    submitted_count: int = 0
    request_count: int = 0
    failed_count: int = 0
    input_failed_count: int = 0
    no_call_count: int = 0
    insufficient_count: int = 0
    skipped_count: int = 0
    successful_count: int = 0


class _PayloadStop(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _RetryDeferred(Exception):
    """A current item remains uncommitted until a later ordinary CRON tick."""


PayloadBuilder = Callable[[ScanPage, int], LlmAnalysisInput | None]
SuggestionValidator = Callable[[tuple, int], None]
_FAILURE_STOPS = frozenset({"AUTH_ERROR", "CONFIG_ERROR", "AUDIT_STORAGE_ERROR",
    "TAG_RELATION_BROKEN", "COMMIT_FAILED", "RESULT_UNKNOWN", "COUNTER_EXHAUSTED",
    "ANALYSIS_ABORTED", "SYNTHETIC_FIXTURE_MISSING", "SYNTHETIC_FIXTURE_INVALID"})


class AutoTagScanService:
    """Provider calls/retry waits stay outside short atomic write transactions."""

    def __init__(self, sessions: Callable[[], Session], analyzer: TagAnalyzer, *, sleep: Callable[[float], Awaitable] = asyncio.sleep):
        self._sessions = sessions
        self._analyzer = analyzer
        self._sleep = sleep

    async def run_synthetic(self, rule_id: int, fixtures: Mapping[int, SyntheticTagScanFixture], context: JobRunContext) -> ScanRunReport:
        def build(page: ScanPage, ledger_id: int):
            fixture = fixtures.get(ledger_id)
            if fixture is None:
                raise _PayloadStop("SYNTHETIC_FIXTURE_MISSING")
            try:
                return self._synthetic_payload(page, fixture, ledger_id=ledger_id)
            except (ValidationError, ValueError, TypeError):
                raise _PayloadStop("SYNTHETIC_FIXTURE_INVALID") from None
        return await self._run(rule_id, context, build)

    async def run_protected(self, rule_id: int, context: JobRunContext, privacy: LlmPrivacyService, *, synthetic_only: bool = False) -> ScanRunReport:
        def build(page: ScanPage, ledger_id: int):
            source = page.protected_sources.get(ledger_id)
            if source is None or synthetic_only and not source.synthetic_allowed:
                raise _PayloadStop("TAG_RELATION_BROKEN")
            return privacy.build_payload(page, source)

        def validate(suggestions: tuple, amount_mode: int):
            privacy.validate_suggestions(suggestions, amount_mode=amount_mode)

        return await self._run(
            rule_id, context, build, suggestion_validator=validate, stop_on_provider_error=True,
        )

    @observed("TAG_SCAN", result_code=lambda report: report.last_error_code or
        (report.stopped_reason if report.stopped_reason in _FAILURE_STOPS else "OK"),
        result_count=lambda report: report.inspected_count)
    async def _run(
        self, rule_id: int, context: JobRunContext, payload_builder: PayloadBuilder,
        *, suggestion_validator: SuggestionValidator | None = None, stop_on_provider_error=False,
    ) -> ScanRunReport:
        counts = _Counts()
        last_error_code = None
        pending = []
        page = None

        async def report(reason):
            nonlocal last_error_code
            if pending:
                context.progress(phase="COMMIT", **asdict(counts))
                try:
                    if not context.is_active():
                        result = None
                        reason = "RULE_TOKEN_CHANGED"
                    else:
                        result = await self._database_work(self._commit_prefix, page, pending)
                except OverflowError:
                    result, reason = None, "COUNTER_EXHAUSTED"
                    context.emit(reason, phase="COMMIT")
                except TargetTagError as error:
                    result, reason = None, "RESULT_UNKNOWN" if error.code == "RESULT_UNKNOWN" else "COMMIT_FAILED"
                    context.emit(reason, phase="COMMIT")
                except Exception:
                    result, reason = None, "COMMIT_FAILED"
                    context.emit(reason, phase="COMMIT")
                if result is not None and result.status == "STALE":
                    reason = result.reason
                    context.emit(reason, phase="COMMIT")
                elif result is not None:
                    if result.reason != "PREFIX_COMMITTED":
                        reason = result.reason
                    for item, committed in zip(pending, result.items):
                        lid = item["ledger_id"]
                        failure, detail = item.get("failure"), item.get("detail")
                        counts.request_count += committed.request_count
                        if committed.reason == "INVALID_SUGGESTION":
                            failure, detail = "OUTPUT_SEMANTIC_INVALID", "INVALID_SUGGESTION"
                        if item["kind"] == "SKIP" or committed.reason not in {"ANALYSIS_COMMITTED", "INVALID_SUGGESTION"}:
                            counts.skipped_count += 1
                            emit("SKIPPED", lid, phase="COMMIT")
                        elif failure:
                            counts.failed_count += 1
                            counts.input_failed_count += int(failure == "INPUT_INVALID")
                            last_error_code = failure
                            emit(failure, lid, phase="COMMIT", detail_code=detail)
                        elif item.get("no_call"):
                            counts.no_call_count += 1
                            counts.successful_count += 1
                            emit("NO_CALL", lid, phase="COMMIT")
                        elif committed.request_count:
                            counts.successful_count += 1
                            emit("SUGGESTION", lid, phase="COMMIT")
                        else:
                            counts.insufficient_count += 1
                            counts.successful_count += 1
                            emit("INSUFFICIENT", lid, phase="COMMIT")
            if reason in {"TAG_RELATION_BROKEN", "CONFIG_CHANGED", "SOURCE_CHANGED"}:
                context.emit(reason, phase="FINISH")
            context.progress(phase="FINISH", **asdict(counts))
            observability.metric("scan_pending", "TAG_SCAN", counts.request_count)
            observability.metric("scan_failed", "TAG_SCAN", counts.failed_count + int(reason in _FAILURE_STOPS))
            return ScanRunReport(rule_id=rule_id, stopped_reason=reason, last_error_code=last_error_code, **asdict(counts))

        context.progress(phase="SCAN")
        page = await self._database_work(self._read_page, rule_id, min(context.page_limit, 100))
        for invalid, reason in (
            (page is None, "RULE_NOT_FOUND"),
            (page is not None and not page.enabled, "RULE_DISABLED"),
            (page is not None and not page.view_active, "VIEW_INACTIVE"),
            (page is not None and not page.model_enabled, "MODEL_DISABLED"),
            (page is not None and not page.targets, "NO_ACTIVE_TARGETS"),
        ):
            if invalid:
                return await report(reason)
        assert page is not None
        token = page.token
        context.progress(page_total=len(page.ledger_ids), rule_revision=token.rule_revision)
        if not page.ledger_ids:
            return await report("NO_DATA")

        def emit(code, ledger_id, *, phase="SCAN", detail_code=None):
            context.emit(
                code, phase=phase, ledger_id=ledger_id,
                rule_revision=token.rule_revision, detail_code=detail_code,
            )

        for ledger_id in page.ledger_ids:
            if not context.may_start_work():
                return await report("SOFT_BUDGET_EXHAUSTED")
            counts.inspected_count += 1
            context.progress(phase="SCAN", attempt=0, **asdict(counts))
            if ledger_id in page.invalid_ledger_ids:
                return await report("TAG_RELATION_BROKEN")
            if self._initial_skip_reason(page, ledger_id) is not None:
                pending.append(dict(ledger_id=ledger_id, kind="SKIP"))
                continue

            try:
                payload = payload_builder(page, ledger_id)
            except _PayloadStop as error:
                return await report(error.reason)
            except (ValidationError, ValueError, TypeError):
                pending.append(dict(ledger_id=ledger_id, kind="ITEM_FAILURE", failure="INPUT_INVALID"))
                continue
            if payload is None:
                pending.append(dict(ledger_id=ledger_id, kind="NO_SUGGESTION", no_call=True))
                continue
            for warning in getattr(payload, "_privacy_warnings", ()):
                emit(warning, ledger_id)
            if not context.may_start_work():
                return await report("SOFT_BUDGET_EXHAUSTED")

            counts.submitted_count += 1
            context.progress(phase="CALL", **asdict(counts))
            suggestions = ()
            failure = None
            detail = None
            try:
                raw_analysis = await self._analyze_with_policy(
                    payload, rule_id=rule_id, model_id=page.model_id, context=context,
                    ledger_id=ledger_id, rule_revision=token.rule_revision,
                )
            except _RetryDeferred:
                emit("RETRY_DEFERRED", ledger_id, phase="RETRY_WAIT")
                return await report("RETRY_DEFERRED")
            except LlmAdapterError as error:
                if error.code in {"AUTH_ERROR", "CONFIG_ERROR", "AUDIT_STORAGE_ERROR"}:
                    emit(error.code, ledger_id, phase="CALL")
                    return await report(error.code)
                kind = "ITEM_FAILURE"
                failure = error.code
                detail = error.details.get("reason_code")
            except Exception:
                await report("ANALYSIS_ABORTED")
                raise
            else:
                # Only malformed output is an item failure. Unexpected analyzer
                # exceptions must retain the current durable checkpoint.
                try:
                    analysis = LlmAnalysisResult.model_validate(raw_analysis)
                    if analysis.item != payload.item:
                        raise LlmAdapterError("Item mismatch", code="OUTPUT_SEMANTIC_INVALID",
                                              details={"reason_code": "ITEM_MISMATCH"})
                    if (analysis.kind == "SUGGESTED") != bool(analysis.suggestions):
                        raise LlmAdapterError("Decision mismatch", code="OUTPUT_SEMANTIC_INVALID",
                                              details={"reason_code": "DECISION_MISMATCH"})
                    kind = "SUGGESTED" if analysis.kind == "SUGGESTED" else "NO_SUGGESTION"
                    suggestions = tuple(analysis.suggestions)
                    if suggestion_validator is not None:
                        suggestion_validator(suggestions, page.amount_mode)
                except LlmAdapterError as error:
                    kind = "ITEM_FAILURE"
                    failure = error.code
                    detail = error.details.get("reason_code")
                except (ValidationError, ValueError, TypeError):
                    kind = "ITEM_FAILURE"
                    failure = "OUTPUT_SEMANTIC_INVALID"
                    detail = "UNSAFE_REASON"

            # A paused/replaced registration must not commit an in-flight response.
            # Budget expiry alone is soft and does allow this item's atomic commit.
            if not context.is_active():
                return await report("RULE_TOKEN_CHANGED")
            pending.append(dict(ledger_id=ledger_id, kind=kind, suggestions=suggestions, failure=failure, detail=detail))
            # Exhausted transport retries stop this page, not all remaining items.
            # Output validation failures are not retryable and can move to the next.
            if stop_on_provider_error and failure in {
                "RATE_LIMIT", "PROVIDER_UNAVAILABLE", "REQUEST_TIMEOUT",
            }:
                return await report(failure)

        return await report("PAGE_COMPLETE")

    async def _analyze_with_policy(
        self, payload: LlmAnalysisInput, *, rule_id: int, model_id: int, context: JobRunContext,
        ledger_id: int, rule_revision: int,
    ) -> LlmAnalysisResult:
        for attempt in range(1, 4):
            if not context.may_start_work():
                raise _RetryDeferred()
            context.progress(phase="CALL", attempt=attempt)
            try:
                return await self._analyzer.analyze(
                    payload, rule_id=rule_id, model_id=model_id,
                    audit_context=PromptAuditContext(
                        run_id=context.run_id, rule_id=rule_id,
                        rule_revision=rule_revision, ledger_id=ledger_id,
                        model_id=model_id, attempt=attempt,
                    ),
                )
            except LlmAdapterError as error:
                if (error.code in {"AUTH_ERROR", "CONFIG_ERROR", "AUDIT_STORAGE_ERROR"}
                        or error.details.get("retryable") is not True or attempt == 3):
                    raise
                raw_delay = error.details.get("retry_after_seconds")
                delay = (
                    float(raw_delay)
                    if type(raw_delay) in (int, float) and math.isfinite(raw_delay) and raw_delay >= 0
                    else float(2 ** (attempt - 1))
                )
                context.emit(
                    error.code, phase="CALL", attempt=attempt,
                    ledger_id=ledger_id, rule_revision=rule_revision,
                )
                if delay >= context.remaining_seconds():
                    raise _RetryDeferred() from None
                context.progress(phase="RETRY_WAIT", attempt=attempt)
                context.emit(
                    "RETRY_SCHEDULED", phase="RETRY_WAIT", attempt=attempt,
                    ledger_id=ledger_id, rule_revision=rule_revision,
                )
                await self._sleep(delay)
        raise AssertionError("retry loop must return or raise")

    def callback(self, rule_id, fixtures) -> JobCallback:
        async def run(context: JobRunContext) -> None:
            await self.run_synthetic(rule_id, fixtures, context)
        return run

    @staticmethod
    async def _database_work(operation, *args):
        return await short_database_work(operation, *args)

    def _read_page(self, rule_id: int, limit: int) -> ScanPage | None:
        with self._sessions() as db:
            with query_budget(db):
                return AutoTagScanMapper(db).read_page(rule_id, limit=limit)

    def _commit_prefix(self, page, outcomes):
        with self._sessions() as db:
            return AutoTagScanMapper(db).commit_prefix(page, outcomes)

    @staticmethod
    def _initial_skip_reason(page: ScanPage, ledger_id: int) -> str | None:
        if ledger_id not in page.active_ledger_ids:
            return "LEDGER_INACTIVE"
        if page.active_tag_states.get(ledger_id, ()) != ("unclassified",):
            return "TARGET_NOT_UNCLASSIFIED"
        if ledger_id in page.existing_request_ids:
            return "REQUEST_ALREADY_EXISTS"
        return None

    @staticmethod
    def _synthetic_payload(page, fixture, *, ledger_id) -> SyntheticLlmAnalysisInput:
        if fixture.ledger_id != ledger_id:
            raise ValueError("fixture ledger_id does not match candidate")
        if fixture.amount.mode != {1: "BAND", 2: "EXACT", 3: "NONE"}[page.amount_mode]:
            raise ValueError("fixture amount mode does not match rule")
        return SyntheticLlmAnalysisInput(
            source=fixture.source, item=fixture.item, direction=fixture.direction,
            merchant=fixture.merchant, summary=fixture.summary, rule_prompt=page.prompt,
            amount=fixture.amount,
            candidates=[LlmCandidate(tag_id=target.tag_id, name=target.name) for target in page.targets],
        )
