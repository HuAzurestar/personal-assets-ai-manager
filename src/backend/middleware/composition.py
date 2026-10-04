"""Application composition is the only middleware file that knows business tasks."""
from dataclasses import dataclass

from backend.mapper.auto_tag_rule_mapper import AutoTagRuleMapper
from backend.mapper.tag_assignment_request_mapper import TagAssignmentRequestMapper
from backend.middleware.platform import PlatformMiddleware
from backend.middleware.runtime import AiRuntime
from backend.middleware.service.prompt_service import PromptService
from backend.middleware.task import TaskRegistry
from backend.service.auto_tag_task import AUTO_TAG_TASK
from backend.service.llm_adapter import LiteLlmAdapter
from backend.service.disclosure_preview_service import DisclosurePreviewService
from backend.middleware.service.management_service import AiManagementService


def _prompt_published(db, key, now):
    if key == AUTO_TAG_TASK.key:
        now, rule_ids = AutoTagRuleMapper(db).advance_for_configuration(
            set(), now=now, prompt_changed=True,
        )
        TagAssignmentRequestMapper(db).cancel_pending_for_rule_ids(rule_ids, now=now)
    return now


@dataclass(frozen=True)
class ApplicationMiddleware:
    ai: AiRuntime
    platform: PlatformMiddleware

    def prompt(self, db):
        return PromptService(db, self.ai.registry, on_publish=_prompt_published)

    def management(self, db):
        return AiManagementService(db, self, {
            AUTO_TAG_TASK.key: lambda session, prompt, request: DisclosurePreviewService(session).preview(
                request, prompt=prompt,
            ),
        })


def create_middleware(sessions, credentials, scheduler, *, executor=None, tasks=None):
    registry = TaskRegistry(tasks if tasks is not None else (AUTO_TAG_TASK,))
    ai = AiRuntime(sessions, credentials, registry, executor or LiteLlmAdapter().executor)
    return ApplicationMiddleware(ai, PlatformMiddleware(sessions, credentials, scheduler))
