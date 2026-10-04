from sqlalchemy import case, func, insert, select, text, update

from backend.entity.base import utc_now
from backend.middleware.entity.ai_invocation import AiInvocation


class AiInvocationMapper:
    def __init__(self, db):
        self.db = db

    def begin_write(self):
        if self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def start(self, values):
        now = utc_now()
        result = self.db.execute(insert(AiInvocation).values(
            **values, status="STARTED", created_time=now, updated_time=now,
        ))
        return int(result.inserted_primary_key[0])

    def finish(self, invocation_id, values):
        result = self.db.execute(update(AiInvocation).where(
            AiInvocation.id == invocation_id, AiInvocation.status == "STARTED",
        ).values(**values, updated_time=utc_now()))
        if result.rowcount != 1:
            raise RuntimeError("Invocation is not open")

    @staticmethod
    def columns():
        # Snapshots and provider output remain local audit data, never HTTP fields.
        return (AiInvocation.id, AiInvocation.task_key, AiInvocation.task_version,
                AiInvocation.prompt_key, AiInvocation.prompt_version,
                AiInvocation.model_id, AiInvocation.model_name, AiInvocation.run_id,
                AiInvocation.attempt, AiInvocation.status, AiInvocation.result_code,
                AiInvocation.error_code, AiInvocation.latency_ms,
                AiInvocation.input_tokens, AiInvocation.output_tokens, AiInvocation.total_tokens,
                AiInvocation.cached_tokens, AiInvocation.cost_usd,
                AiInvocation.created_time, AiInvocation.updated_time)

    def list(self, request):
        total = int(self.db.scalar(select(func.count(AiInvocation.id))) or 0)
        rows = self.db.execute(select(*self.columns()).order_by(AiInvocation.id.desc()).offset(
            (request.page_index - 1) * request.page_size,
        ).limit(request.page_size)).mappings().all()
        return dict(items=[dict(row) for row in rows], total=total,
                    page_index=request.page_index, page_size=request.page_size)

    def usage(self):
        known = AiInvocation.total_tokens >= 0
        row = self.db.execute(select(
            func.count(AiInvocation.id).label("calls"),
            func.sum(case((AiInvocation.status == "SUCCEEDED", 1), else_=0)).label("succeeded"),
            func.sum(case((known, 1), else_=0)).label("calls_with_usage"),
            func.sum(case((known, AiInvocation.total_tokens), else_=0)).label("reported_total_tokens"),
        )).mappings().one()
        # Costs are decimal text. Sum in Service with Decimal, never SQL floats.
        costs = self.db.execute(select(
            AiInvocation.cost_usd, func.count(AiInvocation.id).label("calls"),
        ).where(AiInvocation.cost_usd != "").group_by(AiInvocation.cost_usd)).all()
        return dict(row), costs
