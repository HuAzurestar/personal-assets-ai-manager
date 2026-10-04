"""Immutable prompt snapshots loaded from reviewed, versioned assets."""
from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from backend.error import LlmAdapterError

ASSET_DIR = Path(__file__).resolve().parents[2] / "asset" / "ai_prompt"


class PromptVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=16000)


def bundled_prompt(key: str) -> PromptVersion:
    # Only allow registered asset names, never resolve a caller's path.
    paths = {"auto_tag.classify": "auto_tag.classify.v1.json"}
    if key not in paths:
        raise ValueError("No bundled prompt for this task")
    path = ASSET_DIR / paths[key]
    return PromptVersion.model_validate(json.loads(path.read_text(encoding="utf-8")))


def render_text(template: str, variables: dict[str, object]) -> str:
    """Substitute named values once; user values are never evaluated as templates."""
    def replace(match):
        name = match.group(1)
        if name not in variables:
            raise LlmAdapterError("Prompt template variable is missing", code="CONFIG_ERROR")
        return str(variables[name])
    return re.sub(r"\{\{([a-z][a-z0-9_]*)\}\}", replace, template)
