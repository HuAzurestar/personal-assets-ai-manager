"""Append-only content versions; production publication is one transaction."""
from datetime import timedelta
from sqlalchemy.exc import IntegrityError, OperationalError

from backend.entity.base import utc_now
from backend.error import SettingError
from backend.middleware.mapper.ai_prompt_mapper import AiPromptMapper
from backend.middleware.prompt import PromptVersion


class PromptService:
    def __init__(self, db, registry, on_publish=None):
        self.db = db
        self.mapper = AiPromptMapper(db)
        self.registry = registry
        self.on_publish = on_publish

    def resolve(self, key):
        definition = self.registry.resolve(key)
        row = self.mapper.production(key)
        return (PromptVersion(key=key, version=row["version"], instruction=row["instruction"])
                if row else definition.default_prompt)

    def seed(self):
        # Assets are parsed before taking the SQLite write slot.
        defaults = [task.default_prompt for task in self.registry.definitions()]
        try:
            self.mapper.begin_write()
            now = utc_now()
            self.mapper.seed(defaults, now)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def create(self, request):
        definition = self.registry.resolve(request.prompt_key)
        # Creating a version never changes the active prompt.
        try:
            self.mapper.begin_write()
            now = utc_now()
            prompt_id = self.mapper.create(
                key=request.prompt_key, version=self.mapper.next_version(request.prompt_key, definition.default_prompt.version),
                instruction=request.instruction, note=request.note, state="DRAFT", now=now,
            )
            row = dict(self.mapper.get(prompt_id))
            self.db.commit()
            return row
        except (IntegrityError, OperationalError):
            self.db.rollback()
            raise SettingError(409, "Prompt 写入冲突，请重试", code="AI_PROMPT_WRITE_CONFLICT") from None
        except Exception:
            self.db.rollback()
            raise

    def publish(self, prompt_id, request):
        try:
            self.mapper.begin_write()
            row = self.mapper.get(prompt_id)
            if row is None:
                raise SettingError(404, "Prompt 不存在", code="AI_PROMPT_NOT_FOUND")
            if row["updated_time"] != request.expected_updated_time:
                raise SettingError(409, "Prompt 已改变，请刷新", code="AI_PROMPT_VERSION_CONFLICT")
            if row["state"] == "PRODUCTION":
                self.db.rollback()
                return dict(row)
            active = self.mapper.production(row["prompt_key"])
            default = self.registry.resolve(row["prompt_key"]).default_prompt
            if (active["version"] if active else default.version) != request.expected_production_version:
                raise SettingError(409, "生产版本已改变，请刷新", code="AI_PROMPT_PRODUCTION_CONFLICT")
            now = max(utc_now(), row["updated_time"] + timedelta(microseconds=1),
                      active["updated_time"] + timedelta(microseconds=1) if active else utc_now())
            if self.on_publish:
                now = self.on_publish(self.db, row["prompt_key"], now)
            self.mapper.publish(prompt_id, row["prompt_key"], now)
            result = dict(self.mapper.get(prompt_id))
            self.db.commit()
            return result
        except ValueError:
            self.db.rollback()
            raise SettingError(409, "相关规则无法安全推进版本", code="AI_PROMPT_PUBLICATION_CONFLICT") from None
        except (IntegrityError, OperationalError):
            self.db.rollback()
            raise SettingError(409, "Prompt 写入冲突，请重试", code="AI_PROMPT_WRITE_CONFLICT") from None
        except Exception:
            self.db.rollback()
            raise

    def list(self, request):
        rows, total = self.mapper.list(request.page_index, request.page_size)
        return dict(items=[dict(row) for row in rows], total=total,
                    page_index=request.page_index, page_size=request.page_size)
