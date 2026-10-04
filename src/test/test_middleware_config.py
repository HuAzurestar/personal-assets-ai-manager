import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from pydantic import BaseModel, ConfigDict
from middleware.config import ConfigDefinition, ConfigResolver, ConfigApplier


class Storage:
    def __init__(self):
        self.values, self.token = {"maintenance": 1, "batch": 2}, "v1"

    def read(self):
        return dict(self.values), self.token


class Installer:
    def __init__(self):
        self.prepared, self.installed, self.discarded = [], [], []
        self.disabled = 0

    def prepare(self, snapshots):
        candidate = {s.section: s.value for s in snapshots}
        self.prepared.append(candidate)
        return candidate

    def install(self, candidate):
        self.installed.append(candidate)

    def discard(self, candidate):
        self.discarded.append(candidate)

    def disable(self):
        self.disabled += 1


def runtime(*, hot=True, policy="retain", deployment=None):
    storage, installer, resolver = Storage(), Installer(), ConfigResolver()
    resolver.register(ConfigDefinition("maintenance", int, "1", "resource", sources=("deployment", "persisted", "default"),
                                       env_key="MAINTENANCE", hot_update=hot, failure_policy=policy))
    resolver.register(ConfigDefinition("batch", int, "2", "resource"))
    applier = ConfigApplier(resolver, storage, lambda: deployment or {})
    applier.register("resource", installer)
    return storage, installer, applier


def test_typed_sources_tokens_and_immutable_value():
    resolver = ConfigResolver()
    resolver.register(ConfigDefinition("non-llm", list[int], "[1]", "other"))
    first = resolver.resolve("non-llm", {"non-llm": [2]}, "v1", {})
    copy = first.value
    copy.append(99)
    assert first.value == [2]
    second = resolver.resolve("non-llm", {"non-llm": [2]}, "v2", {})
    assert second.effective_token == first.effective_token and second.storage_token != first.storage_token
    assert resolver.resolve("non-llm", {}, None, {}).origin == "default"
    with pytest.raises(ValueError):
        resolver.resolve("non-llm", {"non-llm": ["2"]}, "bad", {})


def test_deployment_override_saved_change_does_not_reinstall():
    storage, installer, applier = runtime(deployment={"MAINTENANCE": "10"})
    assert applier.reconcile("resource").status == "APPLIED"
    snapshot = applier.snapshots("resource")[0]
    assert snapshot.origin == "deployment" and snapshot.overridden and snapshot.value == 10
    storage.values["maintenance"], storage.token = 60, "v2"
    assert applier.reconcile("resource").applied_token == applier._token(applier.snapshots("resource"))
    assert len(installer.installed) == 1


def test_invalid_override_fails_without_silent_fallback():
    _, installer, applier = runtime(deployment={"MAINTENANCE": "not-json"}, policy="disable")
    state = applier.reconcile("resource")
    assert state.status == "FAILED" and not state.available and installer.disabled == 1
    assert not installer.installed


def test_stale_prepare_is_discarded_and_both_sections_reread():
    storage, installer, applier = runtime()
    original = installer.prepare
    def prepare(snapshots):
        candidate = original(snapshots)
        if len(installer.prepared) == 1:
            storage.values.update(maintenance=3, batch=4)
            storage.token = "v2"
        return candidate
    installer.prepare = prepare
    assert applier.reconcile("resource").status == "APPLIED"
    assert installer.discarded == [{"maintenance": 1, "batch": 2}]
    assert installer.installed == [{"maintenance": 3, "batch": 4}]


def test_scope_serialization_rereads_after_waiting():
    storage, installer, applier = runtime()
    entered, release = threading.Event(), threading.Event()
    original = installer.prepare
    def prepare(snapshots):
        candidate = original(snapshots)
        if len(installer.prepared) == 1:
            entered.set()
            assert release.wait(3)
        return candidate
    installer.prepare = prepare
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(applier.reconcile, "resource")
        assert entered.wait(3)
        second = pool.submit(applier.reconcile, "resource")
        storage.values.update(maintenance=7, batch=8)
        release.set()
        assert first.result(3).status == second.result(3).status == "APPLIED"
    assert installer.installed == [{"maintenance": 7, "batch": 8}]


@pytest.mark.parametrize("policy", ["retain", "disable"])
def test_install_failure_never_claims_applied_and_can_recover(policy):
    storage, installer, applier = runtime(policy=policy)
    old = applier.reconcile("resource")
    original = installer.install
    storage.values["maintenance"] = 3
    def fail(_):
        raise RuntimeError("SECRET must not be exported")
    installer.install = fail
    state = applier.reconcile("resource")
    assert state.status == "FAILED" and state.applied_token == old.applied_token
    assert state.available == (policy == "retain") and "SECRET" not in repr(state)
    installer.install = original
    storage.values["maintenance"] = 1
    assert applier.reconcile("resource").status == "APPLIED"
    assert len(installer.installed) == (2 if policy == "disable" else 1)


def test_startup_section_requires_restart_but_hot_section_does_not():
    storage, installer, applier = runtime(hot=False)
    first = applier.reconcile("resource")
    storage.values["batch"] = 10
    assert applier.reconcile("resource").status == "APPLIED"
    storage.values["maintenance"] = 10
    state = applier.reconcile("resource")
    assert state.status == "NEEDS_RESTART" and state.applied_token != state.desired_token
    assert len(installer.installed) == 2


def test_new_non_llm_config_needs_only_registration():
    class Housekeeping(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        retain_days: int
    storage, installer, applier = runtime()
    applier.resolver.register(ConfigDefinition("housekeeping", Housekeeping, '{"retain_days":7}', "cleaner"))
    cleaner = Installer()
    applier.register("cleaner", cleaner)
    assert applier.reconcile("cleaner").status == "APPLIED"
    assert cleaner.installed == [{"housekeeping": {"retain_days": 7}}]
    assert not installer.installed
