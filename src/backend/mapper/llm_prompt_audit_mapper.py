from __future__ import annotations

import json
from sqlalchemy import insert, text, update, select, or_, func
from sqlalchemy.orm import Session

from backend.entity import LlmPromptAudit
from backend.entity.base import utc_now


class LlmPromptAuditMapper:
    SUMMARY_COLUMNS = tuple(getattr(LlmPromptAudit, name) for name in (
        "id", "source", "operation_id", "run_id", "prompt_id", "prompt_fingerprint", "model_id",
        "status", "error_code", "input_tokens", "output_tokens", "total_tokens", "estimated_cost",
        "created_time", "updated_time"))

    def list_calls(self, page_index, page_size):
        rows = self.db.execute(select(*self.SUMMARY_COLUMNS).order_by(LlmPromptAudit.id.desc())
                               .offset((page_index - 1) * page_size).limit(page_size)).mappings().all()
        total = self.db.scalar(select(func.count(LlmPromptAudit.id)))
        return dict(items=[dict(row) for row in rows], total=total,
                    page_index=page_index, page_size=page_size)

    def statistics(self):
        from sqlalchemy import case
        return dict(self.db.execute(select(
            func.count(LlmPromptAudit.id).label("call_count"),
            func.coalesce(func.sum(case((LlmPromptAudit.status == "SUCCEEDED", 1), else_=0)), 0).label("succeeded_count"),
            func.coalesce(func.sum(case((LlmPromptAudit.status == "ERROR", 1), else_=0)), 0).label("failed_count"),
            func.sum(LlmPromptAudit.input_tokens).label("input_tokens"),
            func.sum(LlmPromptAudit.output_tokens).label("output_tokens"),
            func.sum(LlmPromptAudit.total_tokens).label("total_tokens"),
        )).mappings().one())

    def detail(self, call_id):
        return self.db.execute(select(*self.SUMMARY_COLUMNS, LlmPromptAudit.request_json,
                                     LlmPromptAudit.response_text, LlmPromptAudit.response_truncated,
                                     LlmPromptAudit.metadata_json).where(LlmPromptAudit.id == call_id)).mappings().one_or_none()

    """Write-only persistence for the independent prompt trace table."""

    def __init__(self, db: Session):
        self.db = db

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def start(self, values: dict[str, object]) -> int:
        now = utc_now()
        result = self.db.execute(insert(LlmPromptAudit).values(
            **values, status="STARTED", response_text="", response_truncated=0,
            error_code="", created_time=now, updated_time=now,
        ))
        return int(result.inserted_primary_key[0])

    def finish(
        self, audit_id: int, *, status: str, response_text: str,
        response_truncated: bool, error_code: str,
    ) -> None:
        current = self.db.execute(select(
            LlmPromptAudit.status, LlmPromptAudit.response_text,
            LlmPromptAudit.response_truncated, LlmPromptAudit.error_code,
        ).where(LlmPromptAudit.id == audit_id)).mappings().one_or_none()
        if current is None:
            raise RuntimeError("Prompt audit attempt does not exist")
        if current["status"] != "STARTED":
            if dict(current) == dict(status=status, response_text=response_text,
                                     response_truncated=int(response_truncated), error_code=error_code):
                return
            raise RuntimeError("Conflicting audit completion")
        result = self.db.execute(update(LlmPromptAudit).where(
            LlmPromptAudit.id == audit_id,
            LlmPromptAudit.status == "STARTED",
        ).values(
            status=status,
            response_text=response_text,
            response_truncated=int(response_truncated),
            error_code=error_code,
            updated_time=utc_now(),
        ))
        if result.rowcount != 1:
            raise RuntimeError("Prompt audit attempt is not open")

    def latest_for_item(self, rule_id, ledger_id):
        return self.db.execute(select(
            LlmPromptAudit.id, LlmPromptAudit.status, LlmPromptAudit.operation_id,
            LlmPromptAudit.metadata_json, LlmPromptAudit.request_json,
            LlmPromptAudit.response_text, LlmPromptAudit.response_truncated,
            LlmPromptAudit.error_code,
        ).where(LlmPromptAudit.rule_id == rule_id, LlmPromptAudit.ledger_id == ledger_id)
          .order_by(LlmPromptAudit.id.desc()).limit(1)).mappings().one_or_none()

    def unresolved_for_item(self, rule_id, ledger_id):
        return self.db.execute(select(LlmPromptAudit.id, LlmPromptAudit.metadata_json).where(
            LlmPromptAudit.rule_id == rule_id, LlmPromptAudit.ledger_id == ledger_id,
            or_(LlmPromptAudit.status == "STARTED",
                func.json_extract(LlmPromptAudit.metadata_json, "$.dispatch_state") == "MAY_HAVE_EXECUTED",
                func.json_extract(LlmPromptAudit.metadata_json, "$.recovery_allowed") == False),
        ).order_by(LlmPromptAudit.id).limit(1)).mappings().one_or_none()

    def prevent_recovery(self, call_id):
        raw = self.db.execute(select(LlmPromptAudit.metadata_json).where(LlmPromptAudit.id == call_id)).scalar_one()
        metadata = json.loads(raw)
        metadata["recovery_allowed"] = False
        self.db.execute(update(LlmPromptAudit).where(LlmPromptAudit.id == call_id).values(
            metadata_json=json.dumps(metadata, separators=(",", ":")), updated_time=utc_now()))

    def call_basis(self, call_id):
        return self.db.execute(select(LlmPromptAudit.model_id, LlmPromptAudit.metadata_json,
                                      LlmPromptAudit.rule_id, LlmPromptAudit.ledger_id)
                               .where(LlmPromptAudit.id == call_id)).one()

    def dispatch(self, call_id):
        row = self.db.execute(select(LlmPromptAudit.metadata_json).where(
            LlmPromptAudit.id == call_id, LlmPromptAudit.status == "STARTED")).scalar_one()
        metadata = json.loads(row)
        if metadata.get("dispatch_state") != "NOT_SENT":
            raise RuntimeError("Call already dispatched")
        metadata["dispatch_state"] = "MAY_HAVE_EXECUTED"
        self.db.execute(update(LlmPromptAudit).where(LlmPromptAudit.id == call_id).values(
            metadata_json=json.dumps(metadata, separators=(",", ":")), updated_time=utc_now()))

    def finish_call(self, call_id, *, status, response_text, response_truncated, error_code,
                    metadata, usage):
        row = self.db.execute(select(
            LlmPromptAudit.status, LlmPromptAudit.metadata_json, LlmPromptAudit.response_text,
            LlmPromptAudit.error_code,
        ).where(LlmPromptAudit.id == call_id)).mappings().one()
        previous = json.loads(row["metadata_json"])
        metadata = {**metadata, **{key: previous[key] for key in ("effective_token", "recovery_allowed") if key in previous}}
        if row["status"] != "STARTED":
            if (row["status"] == status and row["response_text"] == response_text
                    and row["error_code"] == error_code and previous == metadata):
                return
            if (previous.get("dispatch_state") != "MAY_HAVE_EXECUTED"
                    or metadata["dispatch_state"] != "RESPONSE_RECEIVED"):
                raise RuntimeError("Conflicting known audit outcome")
        self.db.execute(update(LlmPromptAudit).where(LlmPromptAudit.id == call_id).values(
            status=status, response_text=response_text, response_truncated=int(response_truncated),
            error_code=error_code, metadata_json=json.dumps(metadata, separators=(",", ":")),
            input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"), updated_time=utc_now()))
