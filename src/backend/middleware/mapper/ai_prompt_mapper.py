from sqlalchemy import func, insert, select, text, update

from backend.middleware.entity.ai_prompt import AiPrompt


class AiPromptMapper:
    def __init__(self, db):
        self.db = db

    def begin_write(self):
        if self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    @staticmethod
    def columns():
        return (AiPrompt.id, AiPrompt.prompt_key, AiPrompt.version, AiPrompt.instruction,
                AiPrompt.note, AiPrompt.state, AiPrompt.created_time, AiPrompt.updated_time)

    def get(self, prompt_id):
        return self.db.execute(select(*self.columns()).where(
            AiPrompt.id == prompt_id,
        )).mappings().one_or_none()

    def production(self, key):
        return self.db.execute(select(*self.columns()).where(
            AiPrompt.prompt_key == key, AiPrompt.state == "PRODUCTION",
        )).mappings().one_or_none()

    def next_version(self, key, initial_version=1):
        return max(initial_version, int(self.db.scalar(select(func.max(AiPrompt.version)).where(
            AiPrompt.prompt_key == key,
        )) or 0)) + 1

    def production_versions(self, keys):
        return dict(self.db.execute(select(AiPrompt.prompt_key, AiPrompt.version).where(
            AiPrompt.prompt_key.in_(keys), AiPrompt.state == "PRODUCTION",
        )).all())

    def create(self, *, key, version, instruction, note, state, now):
        result = self.db.execute(insert(AiPrompt).values(
            prompt_key=key, version=version, instruction=instruction, note=note,
            state=state, created_time=now, updated_time=now,
        ))
        return int(result.inserted_primary_key[0])

    def seed(self, defaults, now):
        keys = [default.key for default in defaults]
        existing = set(self.db.scalars(select(AiPrompt.prompt_key).where(
            AiPrompt.prompt_key.in_(keys),
        )).all())
        values = [dict(prompt_key=default.key, version=default.version,
                       instruction=default.instruction, note="内置初始版本",
                       state="PRODUCTION", created_time=now, updated_time=now)
                  for default in defaults if default.key not in existing]
        if values:
            self.db.execute(insert(AiPrompt), values)

    def publish(self, prompt_id, key, now):
        self.db.execute(update(AiPrompt).where(
            AiPrompt.prompt_key == key, AiPrompt.state == "PRODUCTION",
        ).values(state="RETIRED", updated_time=now))
        self.db.execute(update(AiPrompt).where(AiPrompt.id == prompt_id).values(
            state="PRODUCTION", updated_time=now,
        ))

    def list(self, page_index, page_size):
        total = int(self.db.scalar(select(func.count(AiPrompt.id))) or 0)
        rows = self.db.execute(select(*self.columns()).order_by(AiPrompt.id.desc()).offset(
            (page_index - 1) * page_size,
        ).limit(page_size)).mappings().all()
        return rows, total
