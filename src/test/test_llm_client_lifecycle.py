"""Real SDK client-cache regression with an entirely offline HTTP transport."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import sleep

import httpx
import pytest

from backend.service import llm_adapter


@pytest.fixture
def offline_client(monkeypatch, tmp_path):
    import requests
    import tiktoken

    # Tokenization accuracy is not under test here. A local byte vocabulary
    # keeps real SDK transport tests independent of downloaded tokenizer caches.
    encoding = tiktoken.Encoding(
        name="paam-offline-byte", pat_str=r"(?s).",
        mergeable_ranks={bytes([index]): index for index in range(256)},
        special_tokens={},
    )
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path / "empty-tokenizer-cache"))
    monkeypatch.setattr(tiktoken, "get_encoding", lambda *_args, **_kwargs: encoding)
    monkeypatch.setattr(tiktoken, "encoding_for_model", lambda *_args, **_kwargs: encoding)

    def reject_network(*_args, **_kwargs):
        raise AssertionError("SDK transport tests must not download external resources")

    monkeypatch.setattr(requests.sessions.Session, "request", reject_network)
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "true")
    monkeypatch.setenv("HTTPS_PROXY", "http://must-not-be-used.invalid:1")
    monkeypatch.setattr(llm_adapter, "_LITELLM_HTTP_CLIENT", None)
    state = {"clients": [], "requests": [], "statuses": [], "trust_env": []}
    original_client = httpx.Client

    def respond(request):
        assert str(request.url) == "https://provider.invalid/v1/chat/completions"
        state["requests"].append(request)
        status = state["statuses"].pop(0) if state["statuses"] else 200
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "offline failure"}})
        return httpx.Response(200, json={
            "id": "offline", "object": "chat.completion", "created": 1,
            "model": "offline", "choices": [{
                "index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": "{}"},
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    class OfflineClient(original_client):
        def __init__(self, *args, **kwargs):
            state["trust_env"].append(kwargs.get("trust_env"))
            kwargs["transport"] = httpx.MockTransport(respond)
            super().__init__(*args, **kwargs)
            state["clients"].append(self)

    monkeypatch.setattr(httpx, "Client", OfflineClient)
    yield state
    for client in state["clients"]:
        client.close()


def _request(test_name):
    # Distinct fake credentials isolate the SDK cache between tests.
    return {
        "model": "openai/pirc24-offline", "api_base": "https://provider.invalid/v1",
        "api_key": f"offline-fixture-{test_name}",
        "messages": [{"role": "user", "content": "synthetic"}],
        "num_retries": 0, "max_retries": 0, "timeout": 1.0,
    }


def test_real_sdk_reuses_live_transport_for_consecutive_calls(offline_client):
    import litellm

    previous = litellm.client_session
    request = _request("sequential")
    for _ in range(3):
        result = llm_adapter._direct_litellm_completion(**request)
        assert result.choices[0].message.content == "{}"
        assert litellm.client_session is previous
    assert len(offline_client["requests"]) == 3
    assert len(offline_client["clients"]) == 1
    assert offline_client["trust_env"] == [False]
    assert not offline_client["clients"][0].is_closed


def test_provider_failure_does_not_close_cached_transport(offline_client):
    import litellm

    previous = litellm.client_session
    request = _request("failure-recovery")
    offline_client["statuses"] = [503, 200]
    with pytest.raises(litellm.ServiceUnavailableError):
        llm_adapter._direct_litellm_completion(**request)
    assert litellm.client_session is previous
    assert not offline_client["clients"][0].is_closed
    assert llm_adapter._direct_litellm_completion(**request).choices[0].message.content == "{}"
    assert len(offline_client["requests"]) == 2
    assert len(offline_client["clients"]) == 1
    assert litellm.client_session is previous


def test_process_exit_closes_transport_idempotently(offline_client):
    llm_adapter._direct_litellm_completion(**_request("shutdown"))
    client = offline_client["clients"][0]
    assert not client.is_closed
    llm_adapter._close_litellm_http_client()
    llm_adapter._close_litellm_http_client()
    assert client.is_closed


def test_provider_session_swap_is_serialized_and_restored(offline_client, monkeypatch):
    import litellm

    previous = object()
    monkeypatch.setattr(litellm, "client_session", previous)
    guard = Lock()
    active = 0
    maximum = 0
    transports = []

    def complete(**_kwargs):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
            transports.append(litellm.client_session)
        sleep(0.01)
        with guard:
            active -= 1
        return "ok"

    monkeypatch.setattr(litellm, "completion", complete)
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(
            lambda _: llm_adapter._direct_litellm_completion(**_request("parallel")),
            range(3),
        ))
    assert results == ["ok"] * 3
    assert maximum == 1
    assert all(client is offline_client["clients"][0] for client in transports)
    assert litellm.client_session is previous


def test_raw_logging_and_external_callbacks_are_disabled(offline_client, monkeypatch, caplog, capsys):
    import litellm

    def forbidden_callback(*args, **kwargs):
        pytest.fail("external callback received model data")

    monkeypatch.setattr(litellm, "set_verbose", True)
    monkeypatch.setattr(litellm, "log_raw_request_response", True)
    monkeypatch.setattr(litellm, "turn_off_message_logging", False)
    monkeypatch.setattr(litellm, "callbacks", [forbidden_callback])
    request = _request("private-logging")
    request["messages"] = [{"role": "user", "content": "PRIVATE_MESSAGE_CANARY"}]
    llm_adapter._direct_litellm_completion(**request)
    assert litellm.set_verbose is False
    assert litellm.telemetry is False
    assert litellm.turn_off_message_logging is True
    assert litellm.log_raw_request_response is False
    assert litellm.callbacks == []
    output = capsys.readouterr()
    assert "PRIVATE_MESSAGE_CANARY" not in caplog.text + output.out + output.err
