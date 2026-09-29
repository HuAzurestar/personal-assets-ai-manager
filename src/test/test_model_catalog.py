"""Provider model discovery is read-only, bounded and credential-safe."""

import json

import httpx
import pytest

from backend.error import SettingError
from backend.schema.setting import LiteLLMParams, ModelCatalogRequest
from backend.service import model_catalog_service as catalog


class FakeSession:
    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


class FakeReader:
    def __init__(self):
        self.calls = []

    def get_for_provider(self, model_id):
        self.calls.append(model_id)
        return "stored-secret-canary"


def make_service(monkeypatch, response, *, saved=None):
    session, reader, requests, clients = FakeSession(), FakeReader(), [], []
    if saved is not None:
        class FakeMapper:
            def __init__(self, db):
                assert db is session

            def get(self):
                return {"value": {"automation": {"models": [saved]}}}

        monkeypatch.setattr(catalog, "SettingMapper", FakeMapper)

    def handle(request):
        assert session.rollbacks == 1
        requests.append(request)
        return response

    def client_factory(**kwargs):
        clients.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(handle), **{
            key: value for key, value in kwargs.items() if key != "proxy"
        })

    return catalog.ModelCatalogService(session, reader, client_factory), session, reader, requests, clients


def test_siliconflow_catalog_uses_chat_filter_and_saved_secret(monkeypatch):
    saved = {"id": 1, "litellm_params": {
        "api_base": "https://api.siliconflow.cn/v1", "proxy_url": "http://proxy.test:7890",
    }}
    service, session, reader, requests, clients = make_service(
        monkeypatch,
        httpx.Response(200, json={"data": [
            {"id": "Qwen/Qwen3.5-4B", "name": "Qwen 4B"},
            {"id": "Qwen/Qwen3.5-4B"}, {"id": "bad id"},
        ]}), saved=saved,
    )
    result = service.list(ModelCatalogRequest(
        provider="siliconflow", api_base=saved["litellm_params"]["api_base"],
        proxy_url="http://proxy.test:7890", model_id=1,
    ))
    assert [item.id for item in result.items] == ["Qwen/Qwen3.5-4B"]
    assert result.total == 1 and result.chat_only
    assert session.rollbacks == 1 and reader.calls == [1]
    assert clients[0]["proxy"] == "http://proxy.test:7890"
    assert clients[0]["trust_env"] is False and clients[0]["follow_redirects"] is False
    assert str(requests[0].url) == "https://api.siliconflow.cn/v1/models?sub_type=chat"
    assert requests[0].headers["authorization"] == "Bearer stored-secret-canary"


def test_saved_key_is_never_sent_to_changed_endpoint_or_proxy(monkeypatch):
    saved = {"id": 1, "litellm_params": {"api_base": "https://api.deepseek.com"}}
    service, session, reader, requests, _ = make_service(
        monkeypatch, httpx.Response(200, json={"data": []}), saved=saved,
    )
    for base, proxy in [
        ("https://new.example.test/v1", None),
        ("https://api.deepseek.com", "http://proxy.test:7890"),
    ]:
        with pytest.raises(SettingError) as error:
            service.list(ModelCatalogRequest(
                provider="custom", api_base=base, proxy_url=proxy, model_id=1,
            ))
        assert error.value.code == "MODEL_CATALOG_UNSAVED_CONNECTION"
    assert reader.calls == [] and requests == [] and session.rollbacks == 0


def test_opencode_list_excludes_other_api_families(monkeypatch):
    service, _, reader, requests, _ = make_service(
        monkeypatch, httpx.Response(200, json={"data": [
            {"id": "glm-5.1"}, {"id": "gpt-5.5"}, {"id": "claude-sonnet-4-6"},
            {"id": "qwen3.8-max"}, {"id": "jev-1.13"},
        ]}),
    )
    result = service.list(ModelCatalogRequest(
        provider="opencode_console", api_base="https://opencode.ai/inference/openai/v1",
    ))
    assert [item.id for item in result.items] == ["glm-5.1", "qwen3.8-max"]
    assert str(requests[0].url) == "https://opencode.ai/inference/v1/models"
    assert "authorization" not in requests[0].headers and reader.calls == []


@pytest.mark.parametrize("status,code", [
    (401, "MODEL_CATALOG_AUTH_ERROR"), (429, "MODEL_CATALOG_RATE_LIMIT"),
    (302, "MODEL_CATALOG_UNAVAILABLE"),
])
def test_provider_failure_does_not_echo_private_response(monkeypatch, status, code):
    service, _, _, _, _ = make_service(
        monkeypatch, httpx.Response(status, text="stored-secret-canary PRIVATE_BODY"),
    )
    with pytest.raises(SettingError) as error:
        service.list(ModelCatalogRequest(
            provider="deepseek", api_base="https://api.deepseek.com", secret="stored-secret-canary",
        ))
    assert error.value.code == code
    assert "stored-secret-canary" not in str(error.value)
    assert "PRIVATE_BODY" not in str(error.value)


def test_catalog_rejects_wrong_preset_and_oversized_reply(monkeypatch):
    service, _, _, requests, _ = make_service(
        monkeypatch, httpx.Response(200, content=json.dumps({
            "data": [{"id": "x" * 512} for _ in range(3000)],
        }).encode()),
    )
    with pytest.raises(SettingError) as error:
        service.list(ModelCatalogRequest(provider="deepseek", api_base="https://changed.example.test/v1"))
    assert error.value.code == "MODEL_CATALOG_BASE_MISMATCH"
    assert requests == []
    with pytest.raises(SettingError) as error:
        service.list(ModelCatalogRequest(provider="deepseek", api_base="https://api.deepseek.com"))
    assert error.value.code == "MODEL_CATALOG_TOO_LARGE"


@pytest.mark.parametrize("proxy", [
    "socks5://proxy.test:1080", "http://name:pass@proxy.test:7890",
    "http://proxy.test:7890/path", "http://proxy.test:7890?token=x",
    "http://proxy.test:7890#fragment", "http://proxy.test:7890/%61",
])
def test_proxy_validation_rejects_unsafe_urls(proxy):
    with pytest.raises(ValueError):
        LiteLLMParams(model="openai/example", api_base="https://api.example.test/v1", proxy_url=proxy)
