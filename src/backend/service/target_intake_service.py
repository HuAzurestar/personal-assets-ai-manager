from __future__ import annotations

import base64
import hashlib
import logging
from pathlib import Path
from uuid import uuid4

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.core.intake_preview_store import (
    IntakePreviewState,
    target_intake_preview_store,
)
from backend.entity import IMPORT_FILE_STATUS_PENDING
from backend.error import TargetIntakeError
from backend.mapper.target_import_match_mapper import TargetImportMatchMapper
from backend.mapper.target_import_read_mapper import TargetImportReadMapper
from backend.mapper.target_import_write_mapper import TargetImportWriteMapper
from backend.parser.statement_parser import parse_statement
from backend.schema.intake import (
    IntakeConfirmRequest,
    IntakePreviewRequest,
    IntakeReviseRequest,
)
from backend.service.target_economic_service import TargetEconomicService
from backend.smart_import import public_plan


logger = logging.getLogger(__name__)


class TargetIntakeService:
    """PIRC-9 import use case backed only by target Fact tables."""

    def __init__(self, db: Session):
        self.db = db
        self.match_mapper = TargetImportMatchMapper(db)
        self.read_mapper = TargetImportReadMapper(db)
        self.write_mapper = TargetImportWriteMapper(db)
        self.store = target_intake_preview_store

    def preview(self, payload: IntakePreviewRequest) -> dict[str, object]:
        if sum(len(item.content_base64) for item in payload.files) > 140_000_000:
            raise TargetIntakeError(413, "一次最多上传约 100 MB 文件")
        token = uuid4().hex
        uploads: list[dict[str, object]] = []
        pending_inputs = []
        for item in payload.files:
            try:
                content = base64.b64decode(item.content_base64, validate=True)
            except (ValueError, TypeError) as error:
                uploads.append({
                    "document": {
                        "filename": Path(item.filename).name,
                        "error": str(error),
                        "rows": [],
                    },
                })
                continue
            sha256 = hashlib.sha256(content).hexdigest()
            upload = {
                "request": item,
                "content": content,
                "sha256": sha256,
            }
            uploads.append(upload)
            pending_inputs.append({
                "filename": Path(item.filename).name,
                "sha256": sha256,
            })

        pending_files = self._prepare_pending_files(token, pending_inputs)
        documents: list[dict[str, object]] = []
        for upload in uploads:
            if "document" in upload:
                documents.append(upload["document"])
                continue
            item = upload["request"]
            content = upload["content"]
            sha256 = upload["sha256"]
            record = pending_files[sha256]
            try:
                document = parse_statement(
                    content,
                    item.filename,
                    item.password,
                    item.source_type,
                )
            except (ValueError, TypeError) as error:
                document = {
                    "filename": Path(item.filename).name,
                    "sha256": sha256,
                    "error": str(error),
                    "rows": [],
                }
            if record["status"] == IMPORT_FILE_STATUS_PENDING:
                document["transaction_import_file_id"] = record["id"]
            documents.append(document)
        self._update_preview_files(token, documents)
        plan = self.match_mapper.plan(
            documents,
            self.read_mapper.known_accounts(),
        )
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
                self.write_mapper.begin_write()
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
                result = self.write_mapper.write_plan(current, batch_code=token)
                # Keep the Router-owned v1 response name while the physical DB
                # uses transaction_fact.
                result["bill_fact_ids"] = list(result["transaction_fact_ids"])
                TargetEconomicService(self.db).ensure_defaults(
                    result["affected_fact_ids"]
                )
                self.write_mapper.commit()
                state.result = result
                return result
            except TargetIntakeError:
                self.write_mapper.rollback()
                raise
            except (IntegrityError, OperationalError) as error:
                self.write_mapper.rollback()
                logger.exception("Atomic import failed for preview %s", token)
                raise TargetIntakeError(
                    409,
                    "账本正在写入，请重试；本次未部分导入",
                ) from error
            except Exception:
                self.write_mapper.rollback()
                self._mark_pending_files_failed(state.documents)
                raise

    def _prepare_pending_files(
        self,
        batch_code: str,
        uploads: list[dict[str, str]],
    ) -> dict[str, dict[str, int]]:
        try:
            self.write_mapper.begin_write()
            result = self.write_mapper.prepare_files(batch_code, uploads)
            self.write_mapper.commit()
            return result
        except (IntegrityError, OperationalError) as error:
            self.write_mapper.rollback()
            raise TargetIntakeError(
                409,
                "导入文件正在处理，请重试",
            ) from error
        except Exception:
            self.write_mapper.rollback()
            raise

    def _update_preview_files(
        self,
        batch_code: str,
        documents: list[dict[str, object]],
    ) -> None:
        try:
            self.write_mapper.begin_write()
            self.write_mapper.update_preview_files(batch_code, documents)
            self.write_mapper.commit()
        except (IntegrityError, OperationalError) as error:
            self.write_mapper.rollback()
            raise TargetIntakeError(
                409,
                "导入文件状态更新冲突，请重试",
            ) from error
        except Exception:
            self.write_mapper.rollback()
            raise

    def _mark_pending_files_failed(
        self,
        documents: list[dict[str, object]],
    ) -> None:
        file_ids = [
            document["transaction_import_file_id"]
            for document in documents
            if isinstance(document.get("transaction_import_file_id"), int)
        ]
        if not file_ids:
            return
        try:
            self.write_mapper.begin_write()
            self.write_mapper.mark_files_failed(file_ids)
            self.write_mapper.commit()
        except Exception:
            self.write_mapper.rollback()

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
