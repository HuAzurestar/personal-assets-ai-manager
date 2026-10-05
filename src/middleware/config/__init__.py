"""Typed resolution and serialized installation per resource scope."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Protocol
from pydantic import TypeAdapter
from middleware.llm.contract import canonical

MISSING = object()


@dataclass(frozen=True)
class ConfigDefinition:
    section: str
    schema: object
    default_json: str
    effect_scope: str
    sources: tuple[str, ...] = ("persisted", "default")
    env_key: str | None = None
    hot_update: bool = True
    failure_policy: str = "retain"

    def __post_init__(self):
        if (not self.section or not self.effect_scope or not self.sources
                or len(set(self.sources)) != len(self.sources)
                or set(self.sources) - {"deployment", "persisted", "default"}
                or self.failure_policy not in {"retain", "disable"}
                or ("deployment" in self.sources and not self.env_key)):
            raise ValueError("Invalid configuration definition")
        TypeAdapter(self.schema).validate_json(self.default_json, strict=True)


@dataclass(frozen=True)
class ConfigSnapshot:
    section: str
    value_json: str = field(repr=False)
    origin: str
    effective_token: str
    storage_token: str | None
    overridden: bool = False

    @property
    def value(self):
        # Return a copy so the snapshot itself cannot be mutated by consumers.
        return json.loads(self.value_json)


@dataclass(frozen=True)
class ApplyState:
    desired_token: str | None = None
    applied_token: str | None = None
    status: str = "PENDING"
    available: bool = False
    error_code: str | None = None


class ConfigStoragePort(Protocol):
    def read(self) -> tuple[dict, str | None]: ...


class ConfigResolver:
    def __init__(self):
        self.definitions = {}

    def register(self, definition):
        if definition.section in self.definitions:
            raise ValueError("Configuration section already registered")
        self.definitions[definition.section] = definition

    def resolve(self, section, values, storage_token, deployment):
        definition = self.definitions[section]
        selected, origin = MISSING, None
        for source in definition.sources:
            if source == "deployment" and definition.env_key in deployment:
                # Invalid overrides are errors, never a fallback to DB/default.
                selected = json.loads(deployment[definition.env_key])
            elif source == "persisted" and section in values:
                selected = values[section]
            elif source == "default":
                selected = json.loads(definition.default_json)
            if selected is not MISSING:
                origin = source
                break
        if selected is MISSING:
            raise ValueError("Configuration source is missing")
        adapter = TypeAdapter(definition.schema)
        value = adapter.validate_python(selected, strict=True)
        value_json = canonical(adapter.dump_python(value, mode="json"))
        token = hashlib.sha256(canonical([section, origin, json.loads(value_json)]).encode()).hexdigest()
        return ConfigSnapshot(section, value_json, origin, token, storage_token,
                              origin == "deployment" and section in values)


class ConfigApplier:
    def __init__(self, resolver, storage, deployment):
        self.resolver, self.storage, self.deployment = resolver, storage, deployment
        self._installers, self._locks, self._states = {}, {}, {}
        self._installed = {}

    def register(self, scope, installer):
        if scope in self._installers:
            raise ValueError("Installer already registered")
        self._installers[scope] = installer
        self._locks[scope] = RLock()
        self._states[scope] = ApplyState()

    def snapshots(self, scope):
        values, storage_token = self.storage.read()
        deployment = self.deployment()
        return tuple(self.resolver.resolve(section, values, storage_token, deployment)
                     for section, definition in self.resolver.definitions.items() if definition.effect_scope == scope)

    @staticmethod
    def _token(snapshots):
        return hashlib.sha256(canonical(sorted((s.section, s.effective_token) for s in snapshots)).encode()).hexdigest()

    def state(self, scope):
        # State publication is a single immutable reference, never partial fields.
        return self._states[scope]

    def reconcile(self, scope):
        installer = self._installers[scope]
        # Waiting callers do not bring an old snapshot to the installation lock.
        with self._locks[scope]:
            for _ in range(8):
                state = self._states[scope]
                try:
                    snapshots = self.snapshots(scope)
                    token = self._token(snapshots)
                    self._states[scope] = replace(state, desired_token=token, status="PENDING", error_code=None)
                    if token == state.applied_token and state.available:
                        self._states[scope] = replace(state, desired_token=token, status="APPLIED", error_code=None)
                        return self._states[scope]
                    previous = self._installed.get(scope, {})
                    if state.applied_token is not None and any(
                            not self.resolver.definitions[s.section].hot_update
                            and previous.get(s.section) != s.effective_token for s in snapshots):
                        self._states[scope] = replace(state, desired_token=token, status="NEEDS_RESTART")
                        return self._states[scope]
                    candidate = installer.prepare(snapshots)
                    latest = self.snapshots(scope)
                    if self._token(latest) != token:
                        installer.discard(candidate)
                        self._states[scope] = replace(self._states[scope], desired_token=self._token(latest), status="PENDING")
                        continue
                    # Installer publishes a complete resource atomically, or
                    # retains the entire previous resource on failure.
                    installer.install(candidate)
                    self._states[scope] = ApplyState(token, token, "APPLIED", True)
                    self._installed[scope] = {s.section: s.effective_token for s in snapshots}
                    latest_token = self._token(self.snapshots(scope))
                    if latest_token == token:
                        return self._states[scope]
                    self._states[scope] = replace(self._states[scope], desired_token=latest_token, status="PENDING")
                except Exception:
                    available = self._states[scope].available
                    definitions = [d for d in self.resolver.definitions.values() if d.effect_scope == scope]
                    if any(d.failure_policy == "disable" for d in definitions):
                        available = False
                        try:
                            installer.disable()
                        except Exception:
                            pass
                    self._states[scope] = replace(self._states[scope], status="FAILED", available=available,
                                                  error_code="CONFIG_APPLY_FAILED")
                    return self._states[scope]
            # Bounded work on a busy scope; the shared scheduler can retry.
            return self._states[scope]
