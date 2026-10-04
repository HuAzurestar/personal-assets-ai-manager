"""Code-owned task contracts. Registries reject accidental replacements."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel

from backend.error import LlmAdapterError
from backend.middleware.prompt import PromptVersion

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True)
class PreparedTask:
    messages: list[dict[str, str]]
    response_format: dict[str, object]


@dataclass(frozen=True)
class TaskDefinition(Generic[InputT, OutputT]):
    key: str
    version: int
    title: str
    input_type: type[InputT]
    output_type: type[OutputT]
    default_prompt: PromptVersion
    prepare: Callable[[InputT, PromptVersion, str], PreparedTask]
    parse: Callable[[str, InputT], OutputT]
    result_status: Callable[[OutputT], str] = lambda _: "SUCCEEDED"


class TaskRegistry:
    def __init__(self, definitions=()):
        self._definitions: dict[str, TaskDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: TaskDefinition) -> None:
        if definition.default_prompt.key != definition.key:
            raise ValueError("Task and prompt keys must match")
        if definition.key in self._definitions:
            raise ValueError("Task is already registered")
        self._definitions[definition.key] = definition

    def resolve(self, key: str) -> TaskDefinition:
        try:
            return self._definitions[key]
        except KeyError:
            raise LlmAdapterError("The AI task is not registered", code="CONFIG_ERROR") from None

    def definitions(self) -> tuple[TaskDefinition, ...]:
        return tuple(self._definitions.values())
