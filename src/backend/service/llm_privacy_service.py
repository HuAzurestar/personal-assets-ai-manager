"""Conservative field-level privacy boundary for automatic tag analysis."""

from __future__ import annotations

import re
from collections.abc import Mapping

from backend.mapper.auto_tag_scan_mapper import ProtectedScanSource, ScanPage
from backend.schema.llm_analysis import (
    LlmAmountDisclosure,
    LlmCandidate,
    LlmResolvedSuggestion,
    ProtectedLlmAnalysisInput,
)

_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_LABELED_SECRET = re.compile(
    r"(?:账号|账户|卡号|尾号|订单号|流水号|取餐码|验证码|兑换码|收款码|门牌|"
    r"account|card|order|serial|code)\s*[:：#-]?\s*[A-Za-z0-9@._-]+",
    re.IGNORECASE,
)
_PHONE_OR_ACCOUNT = re.compile(r"(?<!\d)\d(?:[ -]?\d){6,}(?!\d)")
_LONG_CODE = re.compile(
    r"\b(?=[A-Za-z0-9_-]{8,}\b)(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+\b"
)
_MONEY = re.compile(
    r"(?:[¥￥$€]\s*[+-]?\d[\d,]*(?:\.\d+)?|"
    r"[+-]?\d[\d,]*(?:\.\d+)?\s*(?:元|圆|块|CNY|RMB|USD))",
    re.IGNORECASE,
)
_CHINESE_MONEY = re.compile(r"[零〇一二两三四五六七八九十百千万亿]+(?:元|圆|块)")
_AMOUNT_HINT = re.compile(r"(?:小额|大额|高额|低额|金额不大|金额较大)")
_SPACE = re.compile(r"\s+")


class LlmPrivacyService:
    """Build the only production payload allowed to cross the model boundary."""

    def __init__(self, disclosure: Mapping[str, object] | None = None):
        value = disclosure if isinstance(disclosure, Mapping) else {}
        bands = value.get("amount_bands", {})
        self._bands = bands if isinstance(bands, Mapping) else {}

    def build_payload(
        self,
        page: ScanPage,
        source: ProtectedScanSource,
    ) -> ProtectedLlmAnalysisInput | None:
        prompt = self.sanitize_text(page.prompt, amount_mode=page.amount_mode)
        # Clean complete source text before applying the provider DTO's limits;
        # clipping first can turn a secret spanning the boundary into plain text.
        merchant = self.sanitize_text(
            source.merchant, amount_mode=page.amount_mode,
        )[:200].strip()
        summary = self.sanitize_text(
            source.summary, amount_mode=page.amount_mode,
        )[:500].strip()
        candidates = []
        for target in page.targets:
            name = self.sanitize_text(target.name, amount_mode=page.amount_mode)
            if name:
                candidates.append(LlmCandidate(tag_id=target.tag_id, name=name))
        if not prompt or not candidates or not (merchant or summary):
            return None
        return ProtectedLlmAnalysisInput(
            source="PROTECTED_LEDGER",
            item="item_1",
            direction=source.direction,
            merchant=merchant,
            summary=summary,
            rule_prompt=prompt,
            amount=self._amount(source, page.amount_mode),
            candidates=candidates,
        )

    def validate_suggestions(
        self,
        suggestions: tuple[LlmResolvedSuggestion, ...],
        *,
        amount_mode: int,
    ) -> None:
        for suggestion in suggestions:
            cleaned = self.sanitize_text(suggestion.reason, amount_mode=amount_mode)
            if not cleaned or cleaned != suggestion.reason.strip():
                raise ValueError("model reason failed the privacy output boundary")

    @staticmethod
    def sanitize_text(value: str, *, amount_mode: int) -> str:
        text = str(value or "")
        text = _URL.sub(" ", text)
        text = _EMAIL.sub(" ", text)
        text = _LABELED_SECRET.sub(" ", text)
        text = _PHONE_OR_ACCOUNT.sub(" ", text)
        text = _LONG_CODE.sub(" ", text)
        text = _CHINESE_MONEY.sub(" ", text)
        text = _MONEY.sub(" ", text)
        if amount_mode == 3:
            text = _AMOUNT_HINT.sub(" ", text)
        text = _SPACE.sub(" ", text).strip(" ,，;；:：/|")
        return text[:4000]

    def _amount(
        self,
        source: ProtectedScanSource,
        amount_mode: int,
    ) -> LlmAmountDisclosure:
        if amount_mode == 2:
            return LlmAmountDisclosure(
                mode="EXACT",
                currency_code=source.currency_code,
                amount_units=source.amount,
            )
        if amount_mode == 3:
            return LlmAmountDisclosure(
                mode="NONE",
                currency_code=source.currency_code,
            )
        boundaries = self._currency_boundaries(source.currency_code)
        if boundaries is None:
            return LlmAmountDisclosure(
                mode="NONE",
                currency_code=source.currency_code,
            )
        absolute = abs(source.amount)
        lower = 0
        upper = None
        for boundary in boundaries:
            if boundary <= absolute:
                lower = boundary
                continue
            upper = boundary
            break
        code = f"{source.currency_code}:{lower}:{upper if upper is not None else 'MAX'}"
        label = f"[{lower},{upper})" if upper is not None else f"[{lower},+∞)"
        return LlmAmountDisclosure(
            mode="BAND",
            currency_code=source.currency_code,
            band_code=code,
            band_label=label,
        )

    def _currency_boundaries(self, currency_code: str) -> tuple[int, ...] | None:
        raw = self._bands.get(currency_code)
        if not isinstance(raw, Mapping):
            return None
        values = raw.get("boundaries", [])
        if not isinstance(values, list):
            return None
        valid = sorted({int(value) for value in values if isinstance(value, int) and value >= 0})
        return tuple(valid) or None
