"""Conservative field-level privacy boundary for automatic tag analysis."""

from __future__ import annotations

import re
import secrets
import unicodedata
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
    r"(?:交易单号|交易订单号|商家订单号|商户单号|商户编号|设备序列号|"
    r"账号|账户|卡号|尾号|订单号|流水号|取餐码|验证码|兑换码|券码|收款码|"
    r"二维码|序列号|手机号|电话|account|card|order|serial|code)"
    r"\s*[:：#=-]?\s*[^\s,，;；。]+",
    re.IGNORECASE,
)
_PHONE_OR_ACCOUNT = re.compile(r"(?<!\d)\d(?:[ -]?\d){6,}(?!\d)")
_LONG_CODE = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9_-]*\d)"
    r"[A-Za-z0-9_-]{8,}(?![A-Za-z0-9])"
)
_MASKED_ACCOUNT = re.compile(r"[\d*xX×•●·-]*\d[\d*xX×•●·-]*[*xX×•●·][\d*xX×•●·-]+")
_IDENTITY = re.compile(
    r"(?:姓名|户名|联系人|收款人|付款人|转账给|付给|还给|转给|收自)"
    r"\s*[:：]?\s*[^\s,，;；。]+"
)
_PRIVATE_FRAGMENT = re.compile(
    r"[^,，;；。\n]*(?:身份证|护照|住址|地址|门牌|"
    r"[\u4e00-\u9fff]{1,15}(?:路|街|巷)\s*\d)[^,，;；。\n]*"
)
_MEASUREMENT = re.compile(
    r"(?<![A-Za-z0-9])\d{1,5}(?:\.\d+)?\s*"
    r"(?:mL|ml|ML|kg|cm|mm|GB|MB|TB|L|g|m|毫升|千克|公斤|升|克|斤|瓶|包|份|个|件|杯)"
    r"(?![A-Za-z])"
)
_BARE_NUMBER = re.compile(r"[+-]?\d[\d,._/ -]*")
_MONEY = re.compile(
    r"(?:[¥￥$€]\s*[+-]?\d[\d,]*(?:\.\d+)?|"
    r"[+-]?\d[\d,]*(?:\.\d+)?\s*(?:元|圆|块|分|角|CNY|RMB|USD))",
    re.IGNORECASE,
)
_CHINESE_MONEY = re.compile(
    r"[零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟萬億]+(?:元|圆|块|角|分)"
)
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
        # Do not expose a personal counterparty merely because it occupies the
        # merchant column. Also remove that known identity from other channels.
        merchant_text = self._normalize_text(source.merchant).strip()
        identity = merchant_text if self._personal_merchant(merchant_text) else ""

        def clean(value: str) -> str:
            value = self._normalize_text(value)
            if identity:
                value = value.replace(identity, "[个人]")
            return self.sanitize_text(value, amount_mode=page.amount_mode)

        prompt = clean(page.prompt)
        # Clean complete source text before applying the provider DTO's limits;
        # clipping first can turn a secret spanning the boundary into plain text.
        merchant = "" if identity else clean(source.merchant)[:200].strip()
        summary = clean(source.summary)[:500].strip()
        candidates = []
        for target in page.targets:
            name = clean(target.name)
            if name:
                candidates.append(LlmCandidate(tag_id=target.tag_id, name=name))
        if not prompt or not candidates or not (
            merchant or summary.replace("[个人]", "").strip(" ,，;；。")
        ):
            return None
        return ProtectedLlmAnalysisInput(
            source="PROTECTED_LEDGER",
            # Independent 128-bit correlation nonce, never an encoding/hash of
            # the Ledger ID. The local scan context owns the Ledger mapping.
            item=f"item_{secrets.token_hex(16)}",
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
            normalized = unicodedata.normalize("NFKC", suggestion.reason).strip()
            if not cleaned or cleaned != normalized:
                raise ValueError("model reason failed the privacy output boundary")

    @staticmethod
    def sanitize_text(value: str, *, amount_mode: int) -> str:
        text = LlmPrivacyService._normalize_text(value)
        # Normalize obfuscation before matching; reserve private-use placeholders
        # internally so input cannot impersonate a protected measurement.
        text = _URL.sub(" ", text)
        text = _EMAIL.sub(" ", text)
        text = _PRIVATE_FRAGMENT.sub(" ", text)
        text = _IDENTITY.sub(" ", text)
        text = _LABELED_SECRET.sub(" ", text)
        text = _PHONE_OR_ACCOUNT.sub(" ", text)
        text = _MASKED_ACCOUNT.sub(" ", text)
        measurements: list[str] = []

        def preserve_measurement(match: re.Match) -> str:
            measurements.append(match.group())
            return f"\ue000{chr(0xE100 + len(measurements))}\ue001"

        text = _MEASUREMENT.sub(preserve_measurement, text)
        text = _LONG_CODE.sub(" ", text)
        text = _CHINESE_MONEY.sub(" ", text)
        text = _MONEY.sub(" ", text)
        # Bare decimals and arithmetic can reconstruct a hidden amount; exact
        # money is permitted only through the structured amount field.
        text = _BARE_NUMBER.sub(" ", text)
        for index, measurement in enumerate(measurements, start=1):
            text = text.replace(f"\ue000{chr(0xE100 + index)}\ue001", measurement)
        if amount_mode == 3:
            text = _AMOUNT_HINT.sub(" ", text)
        text = _SPACE.sub(" ", text).strip(" ,，;；:：/|")
        return text[:4000]

    @staticmethod
    def _normalize_text(value: str) -> str:
        return "".join(
            "" if unicodedata.category(char) in {"Cf", "Co"}
            else " " if unicodedata.category(char).startswith("C") else char
            for char in unicodedata.normalize("NFKC", str(value or ""))
        )

    @staticmethod
    def _personal_merchant(value: str) -> bool:
        name = value.strip()
        business_words = (
            "店", "餐", "饭", "食", "茶", "咖啡", "超市", "商场", "公司", "银行",
            "公交", "地铁", "铁路", "航空", "医院", "学校", "酒店", "外卖", "打车",
            "麦当劳", "肯德基", "支付宝", "微信支付",
        )
        if any(word in name for word in business_words):
            return False
        return bool(
            re.fullmatch(r"[\u4e00-\u9fff·*]{2,4}", name)
            or re.fullmatch(r"[A-Za-z]+(?:[ .-][A-Za-z]+){0,3}", name)
            or any(word in name for word in ("个人", "用户", "先生", "女士"))
        )

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
