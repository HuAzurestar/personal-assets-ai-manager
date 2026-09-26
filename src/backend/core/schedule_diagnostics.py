"""Bounded, allowlisted diagnostics; never a model request/response archive."""

from __future__ import annotations

import json
import re
import secrets
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

MAX_LOG_BYTES = 5 * 1024 * 1024
MAX_ENTRY_BYTES = 2048
PHASES = frozenset({
    "STARTUP", "REGISTER", "QUEUE", "SCAN", "CALL", "RETRY_WAIT", "COMMIT",
    "FINISH", "SHUTDOWN", "WORKER",
})
MESSAGES = {
    "RUN_STARTED": "本轮开始执行。",
    "RUN_COMPLETED": "本轮正常完成。",
    "RUN_CANCELLED": "本轮已取消；未提交结果不视为成功。",
    "BLOCKED_PROBE": "本轮恢复探测已结束，原规则阻塞仍存在；重复错误已聚合。",
    "RUN_ENDED_WITH_ERRORS": "本轮已结束，存在已记录的错误；详见同一诊断编号。",
    "NO_DATA": "正常完成，无待分析数据。",
    "NO_CALL": "本条没有足够的安全业务信息，未调用模型。",
    "SKIPPED": "本条不再满足分析条件，未生成申请。",
    "INSUFFICIENT": "模型正常返回依据不足，未生成申请。",
    "SUGGESTION": "合法建议已提交为待审申请，尚未批准。",
    "INPUT_INVALID": "输入未通过本地校验，未发送给模型。",
    "AMOUNT_BAND_UNCONFIGURED": "该币种未配置分档，本条不披露金额。",
    "CONFIG_ERROR": "模型配置不可用；本条游标未推进，请检查模型设置。",
    "AUTH_ERROR": "供应商拒绝凭据；本条游标未推进，请检查密钥。",
    "MODEL_DISABLED": "模型已停用；请检查模型设置。",
    "NO_ACTIVE_TARGETS": "没有可用候选标签；请检查当前标签维度。",
    "VIEW_INACTIVE": "标签维度已停用，本轮停止。",
    "RULE_NOT_FOUND": "规则不存在，本轮停止。",
    "RULE_DISABLED": "规则已停用，本轮停止。",
    "RULE_TOKEN_CHANGED": "规则版本或游标已变化，旧结果未提交。",
    "CURSOR_ALREADY_ADVANCED": "游标已由其他有效操作推进，旧结果未提交。",
    "REGISTER_FAILED": "配置已保存，但调度注册失败；请检查规则并重新保存。",
    "WORKER_UNHEALTHY": "后台工作线程异常停止，已停止接收任务；请检查并重启服务。",
    "PREVIOUS_RUN_UNKNOWN": "上次进程在本轮结束前中断；结果未知，请核对业务记录。",
    "JOB_CALLBACK_FAILED": "任务执行异常；请使用诊断编号定位，不代表全部未执行。",
    "COMMIT_FAILED": "本条写入失败并已回滚；游标和计数未提交。",
    "COUNTER_EXHAUSTED": "累计计数已达上限，本条未提交。",
    "REQUEST_TIMEOUT": "模型请求超时，供应商端执行情况未知。",
    "PROVIDER_UNAVAILABLE": "模型服务暂时不可用。",
    "RATE_LIMIT": "模型服务限流。",
    "RETRY_SCHEDULED": "在本轮剩余预算内等待重试。",
    "RETRY_DEFERRED": "等待时间超出剩余预算，本条未提交；留到后续 CRON。",
    "SOFT_BUDGET_EXHAUSTED": "本轮启动预算已用完，剩余数据留到后续 CRON。",
    "MODEL_REFUSED": "模型拒绝回答，本条未生成申请。",
    "OUTPUT_EMPTY": "模型返回空内容，本条未生成申请。",
    "OUTPUT_TRUNCATED": "模型输出被截断，本条未生成申请。",
    "OUTPUT_UNEXPECTED": "供应商响应外壳不符合协议，本条未生成申请。",
    "OUTPUT_JSON_INVALID": "模型内容不是严格 JSON，本条未生成申请。",
    "OUTPUT_SCHEMA_INVALID": "模型 JSON 字段结构不符合协议，本条未生成申请。",
    "OUTPUT_SEMANTIC_INVALID": "模型 JSON 未通过业务或隐私校验，本条未生成申请。",
    "ITEM_FAILURE": "本条分析失败，未生成申请。",
    "ACCEPTANCE_DATABASE_REQUIRED": "当前库不满足纯虚构验收条件，已停止扫描。",
    "SYNTHETIC_FIXTURE_MISSING": "缺少虚构验收样例，本轮停止。",
    "SYNTHETIC_FIXTURE_INVALID": "虚构验收样例不符合协议，本轮停止。",
    "UNKNOWN_ERROR": "任务发生未分类错误，请使用诊断编号核对。",
}
DETAIL_MESSAGES = {
    "ITEM_MISMATCH": "临时代号与本次请求不一致。",
    "DECISION_MISMATCH": "decision 与建议数组不一致。",
    "TOO_MANY_SUGGESTIONS": "建议数量超过候选范围。",
    "DUPLICATE_TAG": "同一候选标签重复出现。",
    "UNKNOWN_TAG": "返回了候选范围以外的标签。",
    "UNSAFE_REASON": "理由包含未获准的文字或敏感信息。",
    "INVALID_SUGGESTION": "提交时建议已不符合当前候选条件。",
}
INFO_CODES = frozenset({
    "RUN_STARTED", "RUN_COMPLETED", "NO_DATA", "NO_CALL", "SKIPPED",
    "INSUFFICIENT", "SUGGESTION", "SOFT_BUDGET_EXHAUSTED",
    "BLOCKED_PROBE", "RUN_ENDED_WITH_ERRORS",
})
WARNING_CODES = frozenset({
    "RUN_CANCELLED", "RETRY_SCHEDULED", "RETRY_DEFERRED", "RULE_DISABLED",
    "RULE_TOKEN_CHANGED", "CURSOR_ALREADY_ADVANCED", "AMOUNT_BAND_UNCONFIGURED",
})
_TASK = re.compile(r"^(?:tag-scan:[1-9][0-9]{0,18}|system:[a-z][a-z0-9-]{0,63})$")
_RUN = re.compile(r"^[0-9a-f]{32}$")


def new_run_id() -> str:
    return secrets.token_hex(16)


def safe_code(code: object) -> str:
    return code if isinstance(code, str) and code in MESSAGES else "UNKNOWN_ERROR"


class ScheduleDiagnostics:
    """Three fixed local files, plus a bounded memory fallback on I/O failure.

    Constructed without a directory in unit tests. Production supplies only the
    application-owned DATA_DIR/schedule-logs path, never an HTTP input path.
    """

    def __init__(self, directory: Path | None = None, *, max_bytes: int = MAX_LOG_BYTES):
        if not MAX_ENTRY_BYTES <= max_bytes <= MAX_LOG_BYTES:
            raise ValueError("diagnostic file limit is outside the fixed bounds")
        self._directory = directory
        self._max_bytes = max_bytes
        self._memory: deque[dict] = deque(maxlen=100)
        self._lock = RLock()
        self._io_failed = False
        self._invalid_history = False

    @property
    def health(self) -> str:
        with self._lock:
            return "DEGRADED" if self._io_failed or self._invalid_history else "HEALTHY"

    @property
    def persistent(self) -> bool:
        return self._directory is not None

    def record(
        self, *, run_id: str, task_key: str, phase: str, code: str,
        rule_revision: int | None = None, ledger_id: int | None = None,
        attempt: int | None = None, detail_code: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        code = safe_code(code)
        event = {
            "time": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
            "run_id": run_id if _RUN.fullmatch(run_id) else new_run_id(),
            "task_key": task_key if _TASK.fullmatch(task_key) else "system:unknown",
            "phase": phase if phase in PHASES else "FINISH",
            "code": code,
            "severity": "INFO" if code in INFO_CODES else "WARNING" if code in WARNING_CODES else "ERROR",
            "safe_message": MESSAGES[code],
        }
        for key, value, maximum in (
            ("rule_revision", rule_revision, 2**63 - 1),
            ("ledger_id", ledger_id, 2**63 - 1), ("attempt", attempt, 3),
        ):
            if type(value) is int and 1 <= value <= maximum:
                event[key] = value
        if isinstance(detail_code, str) and detail_code in DETAIL_MESSAGES:
            event["detail_code"] = detail_code
            event["safe_message"] += DETAIL_MESSAGES[detail_code]
        with self._lock:
            self._memory.append(event)
            if self._directory is not None:
                try:
                    self._append(event)
                except (OSError, ValueError):
                    # No exception text or path escapes through fallback logging.
                    self._io_failed = True
                else:
                    self._io_failed = False
        return dict(event)

    def events(self) -> list[dict]:
        with self._lock:
            events = self._read_disk()
            # A failed write still appears, without duplicating successful writes.
            by_value = {json.dumps(item, sort_keys=True): item for item in events}
            by_value.update({json.dumps(item, sort_keys=True): item for item in self._memory})
            # Windows clocks can stamp successive events identically. Break ties
            # by write order so a finish does not appear older than its start.
            return sorted(reversed(list(by_value.values())), key=lambda item: item["time"], reverse=True)

    def recover_interrupted(self) -> None:
        events = self.events()
        finished = {
            item["run_id"] for item in events
            if item["phase"] in {"FINISH", "SHUTDOWN"}
            or item["code"] == "PREVIOUS_RUN_UNKNOWN"
        }
        for item in events:
            if item["code"] == "RUN_STARTED" and item["run_id"] not in finished:
                self.record(
                    run_id=item["run_id"], task_key=item["task_key"],
                    phase="STARTUP", code="PREVIOUS_RUN_UNKNOWN",
                )
                finished.add(item["run_id"])

    def _paths(self) -> tuple[Path, Path, Path]:
        assert self._directory is not None
        paths = tuple(self._directory / name for name in (
            "schedule.jsonl", "schedule.jsonl.1", "schedule.jsonl.2",
        ))
        if self._directory.is_symlink() or any(path.is_symlink() for path in paths):
            raise ValueError("diagnostics cannot follow symbolic links")
        return paths

    def _append(self, event: dict) -> None:
        raw = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(raw) > MAX_ENTRY_BYTES:
            raise ValueError("diagnostic entry exceeds the fixed limit")
        current, previous, oldest = self._paths()
        current.parent.mkdir(parents=True, exist_ok=True)
        if current.exists() and current.stat().st_size + len(raw) > self._max_bytes:
            oldest.unlink(missing_ok=True)
            if previous.exists():
                previous.replace(oldest)
            current.replace(previous)
        with current.open("ab") as stream:
            stream.write(raw)

    def _read_disk(self) -> list[dict]:
        if self._directory is None:
            return []
        events = []
        try:
            for path in reversed(self._paths()):
                if not path.exists():
                    continue
                with path.open("rb") as stream:
                    raw = stream.read(MAX_LOG_BYTES + 1)
                if len(raw) > MAX_LOG_BYTES:
                    self._invalid_history = True
                    continue
                for line in raw.splitlines():
                    item = self._decode(line)
                    if item is None:
                        self._invalid_history = True
                    else:
                        events.append(item)
        except (OSError, ValueError):
            self._io_failed = True
        return events

    @staticmethod
    def _decode(line: bytes) -> dict | None:
        """Revalidate disk input; edited log text is not a trusted UI message."""
        if not line or len(line) > MAX_ENTRY_BYTES:
            return None
        try:
            item = json.loads(line)
            if not isinstance(item, dict) or not (
                isinstance(item.get("run_id"), str) and _RUN.fullmatch(item["run_id"])
                and isinstance(item.get("task_key"), str) and _TASK.fullmatch(item["task_key"])
                and item.get("phase") in PHASES and item.get("code") in MESSAGES
            ):
                return None
            timestamp = datetime.fromisoformat(item["time"])
            if timestamp.tzinfo is None:
                return None
            # Rebuild every display field from the fixed codebook, not disk text.
            local = ScheduleDiagnostics()
            return local.record(
                run_id=item["run_id"], task_key=item["task_key"], phase=item["phase"],
                code=item["code"], rule_revision=item.get("rule_revision"),
                ledger_id=item.get("ledger_id"), attempt=item.get("attempt"),
                detail_code=item.get("detail_code"), now=timestamp,
            )
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            return None
