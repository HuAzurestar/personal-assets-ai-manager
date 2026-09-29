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
from backend.error import LlmAdapterError
from backend.mapper.auto_tag_scan_mapper import (
    AutoTagScanMapper,
    ScanCommitResult,
    ScanPage,
    ScanToken,
)
from backend.schema.auto_tag_scan import SyntheticTagScanFixture
from backend.schema.llm_analysis import (
    LlmAnalysisInput,
    LlmAnalysisResult,
    LlmCandidate,
    SyntheticLlmAnalysisInput,
)
from backend.service.llm_privacy_service import LlmPrivacyService


class TagAnalyzer(Protocol):
    async def analyze(
        self, payload: LlmAnalysisInput, *, rule_id: int, model_id: int,
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
            with self._sessions() as db:
                source = AutoTagScanMapper(db).read_protected_source(ledger_id, synthetic_only=synthetic_only)
            return privacy.build_payload(page, source) if source is not None else None

        def validate(suggestions: tuple, amount_mode: int):
            privacy.validate_suggestions(suggestions, amount_mode=amount_mode)

        return await self._run(
            rule_id, context, build, suggestion_validator=validate, stop_on_provider_error=True,
        )

    async def _run(
        self, rule_id: int, context: JobRunContext, payload_builder: PayloadBuilder,
        *, suggestion_validator: SuggestionValidator | None = None, stop_on_provider_error=False,
    ) -> ScanRunReport:
        counts = _Counts()
        last_error_code = None

        def report(reason):
            context.progress(phase="FINISH", **asdict(counts))
            return ScanRunReport(rule_id=rule_id, stopped_reason=reason, last_error_code=last_error_code, **asdict(counts))

        context.progress(phase="SCAN")
        page = self._read_page(rule_id, min(context.page_limit, 100))
        for invalid, reason in (
            (page is None, "RULE_NOT_FOUND"),
            (page is not None and not page.enabled, "RULE_DISABLED"),
            (page is not None and not page.view_active, "VIEW_INACTIVE"),
            (page is not None and not page.model_enabled, "MODEL_DISABLED"),
            (page is not None and not page.targets, "NO_ACTIVE_TARGETS"),
        ):
            if invalid:
                return report(reason)
        assert page is not None
        token = page.token
        context.progress(page_total=len(page.ledger_ids), rule_revision=token.rule_revision)
        if not page.ledger_ids:
            return report("NO_DATA")

        def emit(code, ledger_id, *, phase="SCAN", detail_code=None):
            context.emit(
                code, phase=phase, ledger_id=ledger_id,
                rule_revision=token.rule_revision, detail_code=detail_code,
            )

        def commit(ledger_id, kind, suggestions=()):
            context.progress(phase="COMMIT", **asdict(counts))
            try:
                return self._commit(token, ledger_id, kind, suggestions)
            except OverflowError:
                emit("COUNTER_EXHAUSTED", ledger_id, phase="COMMIT")
                return ScanCommitResult("STALE", "COUNTER_EXHAUSTED")
            except Exception:  # No raw DB exception/parameters enter diagnostics.
                emit("COMMIT_FAILED", ledger_id, phase="COMMIT")
                return ScanCommitResult("STALE", "COMMIT_FAILED")

        for ledger_id in page.ledger_ids:
            if not context.may_start_work():
                return report("SOFT_BUDGET_EXHAUSTED")
            counts.inspected_count += 1
            context.progress(phase="SCAN", attempt=0, **asdict(counts))
            if self._initial_skip_reason(page, ledger_id) is not None:
                result = commit(ledger_id, "SKIP")
                if result.status == "STALE":
                    return report(result.reason)
                counts.skipped_count += 1
                emit("SKIPPED", ledger_id)
                token = self._advanced(token, ledger_id)
                continue

            try:
                payload = payload_builder(page, ledger_id)
            except _PayloadStop as error:
                return report(error.reason)
            except (ValidationError, ValueError, TypeError):
                result = commit(ledger_id, "ITEM_FAILURE")
                if result.status == "STALE":
                    return report(result.reason)
                if result.reason == "ANALYSIS_COMMITTED":
                    counts.failed_count += 1
                    counts.input_failed_count += 1
                    last_error_code = "INPUT_INVALID"
                    emit("INPUT_INVALID", ledger_id)
                else:
                    counts.skipped_count += 1
                    emit("SKIPPED", ledger_id)
                token = self._advanced(token, ledger_id)
                continue
            if payload is None:
                result = commit(ledger_id, "NO_SUGGESTION")
                if result.status == "STALE":
                    return report(result.reason)
                if result.reason == "ANALYSIS_COMMITTED":
                    counts.no_call_count += 1
                    counts.successful_count += 1
                    emit("NO_CALL", ledger_id)
                else:
                    counts.skipped_count += 1
                    emit("SKIPPED", ledger_id)
                token = self._advanced(token, ledger_id)
                continue
            for warning in getattr(payload, "_privacy_warnings", ()):
                emit(warning, ledger_id)
            if not context.may_start_work():
                return report("SOFT_BUDGET_EXHAUSTED")

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
                return report("RETRY_DEFERRED")
            except LlmAdapterError as error:
                if error.code in {"AUTH_ERROR", "CONFIG_ERROR"}:
                    emit(error.code, ledger_id, phase="CALL")
                    return report(error.code)
                kind = "ITEM_FAILURE"
                failure = error.code
                detail = error.details.get("reason_code")
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
                return report("RULE_TOKEN_CHANGED")
            result = commit(ledger_id, kind, suggestions)
            if result.status == "STALE":
                return report(result.reason)
            counts.request_count += result.request_count
            if result.reason == "INVALID_SUGGESTION":
                failure, detail = "OUTPUT_SEMANTIC_INVALID", "INVALID_SUGGESTION"
            if result.reason not in {"ANALYSIS_COMMITTED", "INVALID_SUGGESTION"}:
                counts.skipped_count += 1
                emit("SKIPPED", ledger_id, phase="COMMIT")
            elif failure is not None:
                counts.failed_count += 1
                last_error_code = failure
                emit(failure, ledger_id, phase="CALL", detail_code=detail)
            elif result.request_count:
                counts.successful_count += 1
                emit("SUGGESTION", ledger_id, phase="COMMIT")
            else:
                counts.insufficient_count += 1
                counts.successful_count += 1
                emit("INSUFFICIENT", ledger_id, phase="COMMIT")
            token = self._advanced(token, ledger_id)
            # Exhausted transport retries stop this page, not all remaining items.
            # Output validation failures are not retryable and can move to the next.
            if stop_on_provider_error and failure in {
                "RATE_LIMIT", "PROVIDER_UNAVAILABLE", "REQUEST_TIMEOUT",
            }:
                return report(failure)

        return report("PAGE_COMPLETE")

    async def _analyze_with_policy(
        self, payload: LlmAnalysisInput, *, rule_id: int, model_id: int, context: JobRunContext,
        ledger_id: int | None = None, rule_revision: int | None = None,
    ) -> LlmAnalysisResult:
        for attempt in range(1, 4):
            if not context.may_start_work():
                raise _RetryDeferred()
            context.progress(phase="CALL", attempt=attempt)
            try:
                return await self._analyzer.analyze(payload, rule_id=rule_id, model_id=model_id)
            except LlmAdapterError as error:
                if (error.code in {"AUTH_ERROR", "CONFIG_ERROR"}
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

    def _read_page(self, rule_id: int, limit: int) -> ScanPage | None:
        with self._sessions() as db:
            return AutoTagScanMapper(db).read_page(rule_id, limit=limit)

    def _commit(self, token, ledger_id, kind, suggestions):
        with self._sessions() as db:
            return AutoTagScanMapper(db).commit_item(
                token, ledger_id=ledger_id, kind=kind, suggestions=suggestions,
            )

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

    @staticmethod
    def _advanced(token: ScanToken, ledger_id: int) -> ScanToken:
        return ScanToken(
            rule_id=token.rule_id, rule_revision=token.rule_revision,
            scan_epoch=token.scan_epoch, scan_after_ledger_id=ledger_id,
        )
