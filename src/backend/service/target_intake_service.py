from __future__ import annotations

import base64
from uuid import uuid4

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.core.intake_preview_store import (
    IntakePreviewState,
    target_intake_preview_store,
)
from backend.error import TargetIntakeError
from backend.mapper.target_import_match_mapper import TargetImportMatchMapper
from backend.mapper.target_import_read_mapper import TargetImportReadMapper
from backend.mapper.target_intake_mapper import TargetIntakeMapper
from backend.parser.statement_parser import parse_statement
from backend.schema.intake import (
    IntakeConfirmRequest,
    IntakePreviewRequest,
    IntakeReviseRequest,
)
from backend.service.target_economic_service import TargetEconomicService
from backend.smart_import import public_plan


class TargetIntakeService:
    """PIRC-9 import use case backed only by target Fact tables."""

    def __init__(self, db: Session):
        self.db = db
        self.match_mapper = TargetImportMatchMapper(db)
        self.read_mapper = TargetImportReadMapper(db)
        self.mapper = TargetIntakeMapper(db)
        self.store = target_intake_preview_store

    def preview(self, payload: IntakePreviewRequest) -> dict[str, object]:
        if sum(len(item.content_base64) for item in payload.files) > 140_000_000:
            raise TargetIntakeError(413, "一次最多上传约 100 MB 文件")
        documents: list[dict[str, object]] = []
        for item in payload.files:
            try:
                content = base64.b64decode(item.content_base64, validate=True)
                documents.append(parse_statement(
                    content,
                    item.filename,
                    item.password,
                    item.source_type,
                ))
            except (ValueError, TypeError) as error:
                documents.append({
                    "filename": item.filename,
                    "error": str(error),
                    "rows": [],
                })
        plan = self.match_mapper.plan(
            documents,
            self.read_mapper.known_accounts(),
        )
        token = uuid4().hex
        self.store.put(IntakePreviewState(
            token=token,
            documents=documents,
            plan=plan,
        ))
        return public_plan(token, plan)

    def revise(self, token: str, payload: IntakeReviseRequest) -> dict[str, object]:
        state = self.store.get(token)
        if state is None or state.result is not None or state.expired:
            raise TargetIntakeError(409, "预览已失效，请重新上传")
        try:
            plan = self.match_mapper.plan(
                state.documents,
                self.read_mapper.known_accounts(),
                payload.accounts,
                payload.decisions,
            )
        except ValueError as error:
            raise TargetIntakeError(422, str(error)) from error
        state.accounts = dict(payload.accounts)
        state.decisions = dict(payload.decisions)
        state.plan = plan
        self.store.replace(state)
        return public_plan(token, plan)

    def confirm(self, token: str, payload: IntakeConfirmRequest) -> dict[str, object]:
        with self.store.locked(token) as state:
            if state is None:
                raise TargetIntakeError(404, "预览不存在，请重新上传")
            if state.result is not None:
                if payload.version != state.plan["version"]:
                    raise TargetIntakeError(409, "确认版本不符")
                return state.result
            if state.expired:
                raise TargetIntakeError(409, "预览已过期，请重新上传")
            if payload.version != state.plan["version"]:
                raise TargetIntakeError(409, "预览已变化，请核对最新预览")
            try:
                self.mapper.begin_write()
                current = self.match_mapper.plan(
                    state.documents,
                    self.read_mapper.known_accounts(),
                    state.accounts,
                    state.decisions,
                )
                if current["version"] != state.plan["version"]:
                    raise TargetIntakeError(409, "账本已变化，请刷新预览后确认")
                if not current["can_confirm"]:
                    raise TargetIntakeError(422, "请先处理预览中标出的错误或歧义")
                result = self.mapper.commit_plan(current, batch_code=token)
                TargetEconomicService(self.db).ensure_defaults(
                    result["affected_fact_ids"]
                )
                self.mapper.commit()
                state.result = result
                return result
            except TargetIntakeError:
                self.mapper.rollback()
                raise
            except (IntegrityError, OperationalError) as error:
                self.mapper.rollback()
                raise TargetIntakeError(
                    409,
                    "账本正在写入，请重试；本次未部分导入",
                ) from error
            except Exception:
                self.mapper.rollback()
                raise

    def history(
        self,
        page: int = 1,
        page_size: int = 20,
        q: str = "",
        account_code: str = "",
    ) -> dict[str, object]:
        return self.read_mapper.history(page, page_size, q, account_code)

    def accounts(self, page: int, page_size: int) -> dict[str, object]:
        return self.read_mapper.accounts(page, page_size)

    def rows(
        self,
        transaction_import_file_id: int,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, object]:
        return self.read_mapper.rows(transaction_import_file_id, page, page_size)
