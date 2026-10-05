"""Backend-owned semantic basis; never contains credentials or raw facts."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExecutionSnapshot:
    rule_revision: int
    prompt_id: str
    prompt_fingerprint: str
    model_json: str = field(repr=False)
    disclosure_json: str = field(repr=False)
