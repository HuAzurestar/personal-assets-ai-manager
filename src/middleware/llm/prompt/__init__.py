"""Read-only prompt resources and one-pass, non-executable rendering."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from middleware.llm.contract import canonical

PLACEHOLDER = re.compile(r"\{\{([a-z][a-z0-9_]{0,63})\}\}")


@dataclass(frozen=True)
class PromptVariableSpec:
    name: str
    kind: str = "text"
    required: bool = True
    max_bytes: int = 64 * 1024

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name) or self.kind not in {"text", "json"} or not 0 < self.max_bytes <= 256 * 1024:
            raise ValueError("Invalid prompt variable declaration")


@dataclass(frozen=True)
class PromptTemplate:
    id: str
    name: str
    content_json: str

    def __post_init__(self):
        if not isinstance(self.id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", self.id) or not isinstance(self.name, str) or not 1 <= len(self.name) <= 120:
            raise ValueError("Invalid prompt identity")
        value = json.loads(self.content_json)
        if not isinstance(value, list) or not 1 <= len(value) <= 64:
            raise ValueError("Invalid prompt message structure")
        for message in value:
            if set(message) != {"role", "content"} or message["role"] not in {"system", "user", "assistant"} or not isinstance(message["content"], str):
                raise ValueError("Invalid prompt message")
            remainder = PLACEHOLDER.sub("", message["content"])
            if "{{" in remainder or "}}" in remainder or len(message["content"].encode()) > 256 * 1024:
                raise ValueError("Invalid prompt placeholder syntax")
        object.__setattr__(self, "content_json", canonical(value))

    @property
    def fingerprint(self):
        return hashlib.sha256(self.content_json.encode("utf-8")).hexdigest()

    def render(self, values, specs):
        declarations = {s.name: s for s in specs}
        if len(declarations) != len(specs) or set(values) - declarations.keys():
            raise ValueError("Unknown prompt variable")
        encoded = {}
        for name, spec in declarations.items():
            if name not in values:
                if spec.required:
                    raise ValueError("Missing required prompt variable")
                encoded[name] = ""
                continue
            value = values[name]
            if spec.kind == "text" and not isinstance(value, str):
                raise ValueError("Invalid prompt variable type")
            encoded[name] = value if spec.kind == "text" else canonical(value)
            if len(encoded[name].encode()) > spec.max_bytes:
                raise ValueError("Prompt variable exceeds declared limit")
        result = json.loads(self.content_json)
        for message in result:
            names = set(PLACEHOLDER.findall(message["content"]))
            if names - declarations.keys():
                raise ValueError("Template incompatible with caller contract")
            # re.sub never scans replacement text, so {{...}} in data is literal.
            message["content"] = PLACEHOLDER.sub(lambda m: encoded[m[1]], message["content"])
        return result


class FilePromptStore:
    def __init__(self, directory=None):
        root = Path(directory) if directory is not None else Path(__file__).parent / "resource"
        prefix_path = root / "safety_prefix.txt"
        prefix = prefix_path.read_text(encoding="utf-8").strip() if prefix_path.exists() else ""
        self._templates = {}
        for path in sorted(root.glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            if prefix:
                if value["messages"][0]["role"] != "system":
                    raise ValueError("Shared safety prefix requires a system message")
                value["messages"][0]["content"] = prefix + "\n" + value["messages"][0]["content"]
            template = PromptTemplate(value["id"], value["name"], canonical(value["messages"]))
            if template.id in self._templates:
                raise ValueError("Duplicate prompt resource")
            self._templates[template.id] = template

    def get(self, prompt_id):
        return self._templates[prompt_id]

    def list(self):
        return tuple(self._templates.values())


prompt_store = FilePromptStore()
TAG_VARIABLES = (PromptVariableSpec("disclosed_input", "json"),)
