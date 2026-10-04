"""Management use cases expose metadata and authored fixture previews."""
from backend.error import SettingError
from backend.middleware.prompt import PromptVersion


class AiManagementService:
    def __init__(self, db, middleware, previewers):
        self.db = db
        self.middleware = middleware
        self.prompts = middleware.prompt(db)
        self.previewers = previewers

    def tasks(self, request):
        definitions = self.middleware.ai.registry.definitions()
        start = (request.page_index - 1) * request.page_size
        page = definitions[start:start + request.page_size]
        active = self.prompts.mapper.production_versions([item.key for item in page])
        return dict(items=[dict(
            key=task.key, version=task.version, title=task.title,
            input_schema=task.input_type.__name__, output_schema=task.output_type.__name__,
            prompt_key=task.default_prompt.key,
            production_version=active.get(task.key, task.default_prompt.version),
        ) for task in page], total=len(definitions),
                    page_index=request.page_index, page_size=request.page_size)

    def preview(self, prompt_id, request):
        row = self.prompts.mapper.get(prompt_id)
        if row is None:
            raise SettingError(404, "Prompt 不存在", code="AI_PROMPT_NOT_FOUND")
        previewer = self.previewers.get(row["prompt_key"])
        if previewer is None:
            raise SettingError(422, "任务尚未定义虚构样例", code="AI_FIXTURE_NOT_DEFINED")
        prompt = PromptVersion(key=row["prompt_key"], version=row["version"], instruction=row["instruction"])
        return previewer(self.db, prompt, request)
