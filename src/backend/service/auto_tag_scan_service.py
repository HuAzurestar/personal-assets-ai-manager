"""Bounded automatic-tag scan orchestration for test and scheduled runs."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.core.job_scheduler import JobCallback, JobRunContext
from backend.error import LlmAdapterError
from backend.mapper.auto_tag_scan_mapper import (
    AutoTagScanMapper,
    ScanCommitKind,
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

logger = logging.getLogger(__name__)


class TagAnalyzer(Protocol):
    async def analyze(
        self,
        payload: LlmAnalysisInput,
        *,
        rule_id: int,
        model_id: int,
    ) -> LlmAnalysisResult: ...


@dataclass(frozen=True, slots=True)
class ScanRunReport:
    rule_id: int
    inspected_count: int
    submitted_count: int
    request_count: int
    failed_count: int
    stopped_reason: str


class _PayloadStop(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


PayloadBuilder = Callable[[ScanPage, int], LlmAnalysisInput | None]
SuggestionValidator = Callable[[tuple, int], None]


class AutoTagScanService:
    """Run one bounded page with provider calls outside write transactions."""

    def __init__(
        self,
        sessions: Callable[[], Session],
        analyzer: TagAnalyzer,
    ):
        self._sessions = sessions
        self._analyzer = analyzer

    async def run_synthetic(
        self,
        rule_id: int,
        fixtures: Mapping[int, SyntheticTagScanFixture],
        context: JobRunContext,
    ) -> ScanRunReport:
        def build(page: ScanPage, ledger_id: int) -> SyntheticLlmAnalysisInput:
            fixture = fixtures.get(ledger_id)
            if fixture is None:
                raise _PayloadStop("SYNTHETIC_FIXTURE_MISSING")
            try:
                return self._synthetic_payload(page, fixture, ledger_id=ledger_id)
            except (ValidationError, ValueError, TypeError) as error:
                raise _PayloadStop("SYNTHETIC_FIXTURE_INVALID") from error

        return await self._run(rule_id, context, build)

    async def run_protected(
        self,
        rule_id: int,
        context: JobRunContext,
        privacy: LlmPrivacyService,
        *,
        synthetic_only: bool = False,
    ) -> ScanRunReport:
        def build(page: ScanPage, ledger_id: int):
            with self._sessions() as db:
                source = AutoTagScanMapper(db).read_protected_source(
                    ledger_id, synthetic_only=synthetic_only,
                )
            if source is None:
                return None
            return privacy.build_payload(page, source)

        def validate(suggestions: tuple, amount_mode: int) -> None:
            privacy.validate_suggestions(suggestions, amount_mode=amount_mode)

        return await self._run(
            rule_id,
            context,
            build,
            suggestion_validator=validate,
            stop_on_provider_error=True,
        )

    async def _run(
        self,
        rule_id: int,
        context: JobRunContext,
        payload_builder: PayloadBuilder,
        *,
        suggestion_validator: SuggestionValidator | None = None,
        stop_on_provider_error: bool = False,
    ) -> ScanRunReport:
        page = self._read_page(rule_id, min(context.page_limit, 100))
        if page is None:
            return ScanRunReport(rule_id, 0, 0, 0, 0, "RULE_NOT_FOUND")
        if not page.enabled:
            return ScanRunReport(rule_id, 0, 0, 0, 0, "RULE_DISABLED")
        if not page.view_active:
            return ScanRunReport(rule_id, 0, 0, 0, 0, "VIEW_INACTIVE")
        if not page.model_enabled:
            return ScanRunReport(rule_id, 0, 0, 0, 0, "MODEL_DISABLED")
        if not page.targets:
            return ScanRunReport(rule_id, 0, 0, 0, 0, "NO_ACTIVE_TARGETS")

        inspected = submitted = request_count = failed = 0
        token = page.token
        for ledger_id in page.ledger_ids:
            inspected += 1
            skip_reason = self._initial_skip_reason(page, ledger_id)
            if skip_reason is not None:
                result = self._commit(token, ledger_id, "SKIP", ())
                if result.status == "STALE":
                    return ScanRunReport(
                        rule_id, inspected, submitted, request_count, failed,
                        result.reason,
                    )
                token = self._advanced(token, ledger_id)
                continue

            if not context.may_start_work():
                return ScanRunReport(
                    rule_id, inspected - 1, submitted, request_count, failed,
                    "SOFT_BUDGET_EXHAUSTED",
                )
            try:
                payload = payload_builder(page, ledger_id)
            except _PayloadStop as error:
                return ScanRunReport(
                    rule_id, inspected, submitted, request_count, failed,
                    error.reason,
                )
            if payload is None:
                result = self._commit(token, ledger_id, "NO_SUGGESTION", ())
                if result.status == "STALE":
                    return ScanRunReport(
                        rule_id, inspected, submitted, request_count, failed,
                        result.reason,
                    )
                token = self._advanced(token, ledger_id)
                continue

            submitted += 1
            kind: ScanCommitKind
            suggestions = ()
            item_failed = False
            provider_error_code = None
            try:
                analysis = LlmAnalysisResult.model_validate(
                    await self._analyze_with_policy(
                        payload,
                        rule_id=rule_id,
                        model_id=page.model_id,
                        context=context,
                    )
                )
                if analysis.item != payload.item:
                    raise ValueError("analysis item does not match request")
                if analysis.kind == "SUGGESTED":
                    if not analysis.suggestions:
                        raise ValueError("suggested analysis requires suggestions")
                    kind = "SUGGESTED"
                    suggestions = tuple(analysis.suggestions)
                    if suggestion_validator is not None:
                        suggestion_validator(suggestions, page.amount_mode)
                else:
                    if analysis.suggestions:
                        raise ValueError("insufficient analysis cannot suggest tags")
                    kind = "NO_SUGGESTION"
            except LlmAdapterError as error:
                logger.warning(
                    "Auto-tag provider failure rule=%s ledger=%s code=%s",
                    rule_id,
                    ledger_id,
                    error.code,
                )
                if error.code in {"AUTH_ERROR", "CONFIG_ERROR"}:
                    return ScanRunReport(
                        rule_id, inspected, submitted, request_count, failed,
                        error.code,
                    )
                kind = "ITEM_FAILURE"
                item_failed = True
                provider_error_code = error.code
            except (ValidationError, ValueError, TypeError) as error:
                logger.warning(
                    "Auto-tag output rejected rule=%s ledger=%s error_type=%s",
                    rule_id,
                    ledger_id,
                    type(error).__name__,
                )
                kind = "ITEM_FAILURE"
                item_failed = True
            except Exception as error:  # noqa: BLE001 - isolate one item
                logger.warning(
                    "Auto-tag item failed rule=%s ledger=%s error_type=%s",
                    rule_id,
                    ledger_id,
                    type(error).__name__,
                )
                kind = "ITEM_FAILURE"
                item_failed = True

            result = self._commit(token, ledger_id, kind, suggestions)
            if result.status == "STALE":
                return ScanRunReport(
                    rule_id, inspected, submitted, request_count, failed,
                    result.reason,
                )
            request_count += result.request_count
            if item_failed or result.reason == "INVALID_SUGGESTION":
                failed += 1
            if result.reason == "INVALID_SUGGESTION":
                logger.warning(
                    "Auto-tag suggestion rejected rule=%s ledger=%s",
                    rule_id,
                    ledger_id,
                )
            token = self._advanced(token, ledger_id)
            if stop_on_provider_error and provider_error_code is not None:
                return ScanRunReport(
                    rule_id,
                    inspected,
                    submitted,
                    request_count,
                    failed,
                    provider_error_code,
                )

        return ScanRunReport(
            rule_id, inspected, submitted, request_count, failed, "PAGE_COMPLETE"
        )

    async def _analyze_with_policy(
        self,
        payload: LlmAnalysisInput,
        *,
        rule_id: int,
        model_id: int,
        context: JobRunContext,
    ) -> LlmAnalysisResult:
        attempts = 0
        while True:
            attempts += 1
            try:
                return await self._analyzer.analyze(
                    payload,
                    rule_id=rule_id,
                    model_id=model_id,
                )
            except LlmAdapterError as error:
                retryable = error.details.get("retryable") is True
                if (
                    error.code in {"AUTH_ERROR", "CONFIG_ERROR"}
                    or not retryable
                    or attempts >= 3
                    or not context.may_start_work()
                ):
                    raise

    def callback(
        self,
        rule_id: int,
        fixtures: Mapping[int, SyntheticTagScanFixture],
    ) -> JobCallback:
        async def run(context: JobRunContext) -> None:
            await self.run_synthetic(rule_id, fixtures, context)

        return run

    def _read_page(self, rule_id: int, limit: int) -> ScanPage | None:
        with self._sessions() as db:
            return AutoTagScanMapper(db).read_page(rule_id, limit=limit)

    def _commit(self, token, ledger_id, kind, suggestions):
        with self._sessions() as db:
            return AutoTagScanMapper(db).commit_item(
                token,
                ledger_id=ledger_id,
                kind=kind,
                suggestions=suggestions,
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
    def _synthetic_payload(
        page: ScanPage,
        fixture: SyntheticTagScanFixture,
        *,
        ledger_id: int,
    ) -> SyntheticLlmAnalysisInput:
        if fixture.ledger_id != ledger_id:
            raise ValueError("fixture ledger_id does not match candidate")
        expected_mode = {1: "BAND", 2: "EXACT", 3: "NONE"}[page.amount_mode]
        if fixture.amount.mode != expected_mode:
            raise ValueError(
                f"fixture amount mode {fixture.amount.mode} does not match rule "
                f"mode {expected_mode}"
            )
        return SyntheticLlmAnalysisInput(
            source=fixture.source,
            item=fixture.item,
            direction=fixture.direction,
            merchant=fixture.merchant,
            summary=fixture.summary,
            rule_prompt=page.prompt,
            amount=fixture.amount,
            candidates=[
                LlmCandidate(tag_id=target.tag_id, name=target.name)
                for target in page.targets
            ],
        )

    @staticmethod
    def _advanced(token: ScanToken, ledger_id: int) -> ScanToken:
        return ScanToken(
            rule_id=token.rule_id,
            rule_revision=token.rule_revision,
            scan_epoch=token.scan_epoch,
            scan_after_ledger_id=ledger_id,
        )
