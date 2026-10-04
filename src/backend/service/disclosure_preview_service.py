"""Preview the existing privacy/message boundary without network or business writes."""

from datetime import datetime

from sqlalchemy.orm import Session

from backend.core.money import amount_from_decimal
from backend.error import SettingError
from backend.mapper.auto_tag_scan_mapper import (
    ProtectedScanSource,
    ScanPage,
    ScanTarget,
    ScanToken,
)
from backend.mapper.setting_mapper import SettingMapper
from backend.schema.disclosure_preview import (
    DisclosurePreviewRead,
    DisclosurePreviewRequest,
    DisclosurePreviewSample,
)
from backend.service.auto_tag_task import AUTO_TAG_TASK, build_messages
from backend.middleware.service.prompt_service import PromptService
from backend.middleware.task import TaskRegistry
from backend.service.llm_privacy_service import LlmPrivacyService
from backend.service.setting_service import DEFAULT_DISCLOSURE


# All text is authored test data. The endpoint never accepts a Ledger ID or source text.
_SAMPLES = {
    "MEAL_SMALL": ("虚构日常餐饮", "29", "验收虚构餐厅", "午餐；订单号 DEMO12345678；29元"),
    "MEAL_LARGE": ("虚构高额聚餐", "3500", "验收虚构餐厅", "多人聚餐；订单号 DEMO98765432；3500元"),
    "NON_MEAL_SMALL": ("虚构小额非餐饮", "20", "验收虚构文具店", "文具；订单号 DEMO12345678；20元"),
    "NO_CONTEXT": ("虚构无业务语义", "29", "王小明", "账号：6222000012345678"),
}


class DisclosurePreviewService:
    def __init__(self, db: Session):
        # Preview reads only the setting, never credentials or ledger rows.
        self.mapper = SettingMapper(db)
        self.prompts = PromptService(db, TaskRegistry((AUTO_TAG_TASK,)))

    def preview(self, request: DisclosurePreviewRequest, *, prompt=None) -> DisclosurePreviewRead:
        try:
            setting = self.mapper.get()
        except (TypeError, ValueError) as error:
            raise SettingError(
                500, "stored setting is invalid", code="SETTING_DATA_INVALID",
            ) from error
        if setting is None:
            disclosure = DEFAULT_DISCLOSURE
        else:
            automation = setting["value"].get("automation", {})
            if not isinstance(automation, dict):
                raise SettingError(
                    500, "stored automation setting is invalid", code="SETTING_DATA_INVALID",
                )
            disclosure = automation.get("disclosure", {})
        label, amount, merchant, summary = _SAMPLES[request.sample]
        sample = DisclosurePreviewSample(
            label=label,
            amount=amount_from_decimal(amount, request.currency_code),
            currency_code=request.currency_code,
            merchant=merchant,
            summary=summary,
        )
        page = ScanPage(
            token=ScanToken(1, 1, 1, 0), enabled=True, view_active=True,
            model_enabled=True, view_id=1, model_id=1,
            prompt="只根据安全用途语义从候选标签选择，金额不能单独决定标签。",
            amount_mode=request.amount_mode, ledger_ids=(1,),
            active_ledger_ids=frozenset({1}), active_tag_states={},
            existing_request_ids=frozenset(),
            targets=(ScanTarget(1, "餐饮"), ScanTarget(2, "文具")),
        )
        payload = LlmPrivacyService(disclosure).build_payload(page, ProtectedScanSource(
            direction="OUT", amount=sample.amount, currency_code=sample.currency_code,
            merchant=sample.merchant, summary=sample.summary,
            occurred_time=datetime(2030, 1, 2, 12, 34, 56),
        ))
        warnings = [
            "固定虚构样例，仅使用已保存配置；未请求模型、未扫描账本、未创建打标申请。",
            "日期按已保存的DAY/MONTH/NONE策略披露，不发送时分秒。",
        ]
        if payload is None:
            warnings.append("清洗后没有足够业务语义，不会请求模型。")
        elif request.amount_mode == 1 and payload.amount.mode == "NONE":
            warnings.append("该币种未配置区间，省略金额；不套用其他币种阈值。")
        return DisclosurePreviewRead(
            sample_id=request.sample, sample=sample, input_eligible=payload is not None,
            messages=build_messages(payload, prompt=prompt or self.prompts.resolve(AUTO_TAG_TASK.key))
            if payload is not None else [], warnings=warnings,
        )
