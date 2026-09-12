from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.mappers.intake_mapper import IntakeMapper
from app.schemas.intake import (
    IntakeConfirmRequest,
    IntakePreviewRequest,
    IntakeReviseRequest,
)
from app.smart_import import public_plan
from app.statement_parser import parse_statement


class IntakeError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class IntakeService:
    """Multi-source preview and confirmation without HTTP concerns."""

    def __init__(self, db: Session):
        self.mapper = IntakeMapper(db)

    def preview(self, payload: IntakePreviewRequest) -> dict[str, object]:
        if sum(len(item.content_base64) for item in payload.files) > 140_000_000:
            raise IntakeError(413, "一次最多上传约 100 MB 文件")
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
        plan = self.mapper.plan(documents)
        token = uuid4().hex
        self.mapper.create_preview(token, documents, plan)
        self.mapper.commit()
        return public_plan(token, plan)

    def revise(self, token: str, payload: IntakeReviseRequest) -> dict[str, object]:
        stage = self.mapper.preview(token)
        if (
            stage is None
            or stage.result_json
            or stage.created_time < datetime.now() - timedelta(hours=24)
        ):
            raise IntakeError(409, "预览已失效，请重新上传")
        stored = json.loads(stage.payload_json)
        documents = stored["documents"] if isinstance(stored, dict) else stored
        try:
            plan = self.mapper.plan(documents, payload.accounts, payload.decisions)
        except ValueError as error:
            raise IntakeError(422, str(error)) from error
        self.mapper.revise_preview(
            stage,
            documents,
            payload.accounts,
            payload.decisions,
            plan,
        )
        self.mapper.commit()
        return public_plan(token, plan)

    def confirm(self, token: str, payload: IntakeConfirmRequest) -> dict[str, object]:
        try:
            self.mapper.begin_write()
            stage = self.mapper.preview(token)
            if stage is None:
                raise IntakeError(404, "预览不存在，请重新上传")
            previous = json.loads(stage.plan_json)
            if stage.result_json:
                if payload.version != previous["version"]:
                    raise IntakeError(409, "确认版本不符")
                return json.loads(stage.result_json)
            if stage.created_time < datetime.now() - timedelta(hours=24):
                raise IntakeError(409, "预览已过期，请重新上传")
            if payload.version != previous["version"]:
                raise IntakeError(409, "预览已变化，请核对最新预览")
            stored = json.loads(stage.payload_json)
            documents = stored["documents"] if isinstance(stored, dict) else stored
            current = self.mapper.plan(
                documents,
                stored.get("accounts") if isinstance(stored, dict) else None,
                stored.get("decisions") if isinstance(stored, dict) else None,
            )
            if current["version"] != previous["version"]:
                raise IntakeError(409, "账本已变化，请刷新预览后确认")
            if not current["can_confirm"]:
                raise IntakeError(422, "请先处理预览中标出的错误或歧义")
            result = self.mapper.commit_plan(current)
            stage.result_json = json.dumps(
                result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            stage.payload_json = "[]"
            stage.plan_json = json.dumps({
                "version": current["version"],
                "counts": current["counts"],
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            self.mapper.commit()
            return result
        except IntakeError:
            self.mapper.rollback()
            raise
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise IntakeError(
                409,
                "账本正在写入，请重试；本次未部分导入",
            ) from error
        except Exception:
            self.mapper.rollback()
            raise

    def history(self) -> list[dict[str, object]]:
        return self.mapper.history()

    def accounts(self) -> list[dict[str, object]]:
        return self.mapper.accounts()

    def rows(self, batch_id: int) -> list[dict[str, object]]:
        return self.mapper.rows(batch_id)
