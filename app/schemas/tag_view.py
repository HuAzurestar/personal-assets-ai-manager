from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TagViewDefinitionVO:
    id: int
    name: str
    system_name: str
    archived: bool


@dataclass(frozen=True, slots=True)
class TagValueDefinitionVO:
    id: int
    view_id: int
    name: str
    system_name: str
    is_unclassified: bool
    archived: bool
