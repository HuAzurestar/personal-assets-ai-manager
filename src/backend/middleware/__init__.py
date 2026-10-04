"""Public middleware contracts; construct the shared instance at startup."""

from backend.middleware.prompt import PromptVersion
from backend.middleware.task import PreparedTask, TaskDefinition, TaskRegistry

def create_middleware(*args, **kwargs):
    from backend.middleware.composition import create_middleware as create
    return create(*args, **kwargs)


def __getattr__(name):
    # Entities register during TargetBase initialization; defer runtime imports
    # until application composition so storage imports cannot form a cycle.
    if name in {"AiRuntime", "ExecutionContext"}:
        from backend.middleware.runtime import AiRuntime, ExecutionContext
        return {"AiRuntime": AiRuntime, "ExecutionContext": ExecutionContext}[name]
    raise AttributeError(name)


__all__ = ["AiRuntime", "ExecutionContext", "PromptVersion", "PreparedTask", "TaskDefinition", "TaskRegistry", "create_middleware"]
