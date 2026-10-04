"""Connection checks cannot scan bills, leak credentials or retry a request."""
import json
from threading import Event
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event

from backend.router.system_setting import get_connection_secret_reader
from backend.service import model_connection_service as connection
from test_setting_api import _client, _model_payload, setting_runtime  # noqa: F401
from test_llm_client_lifecycle import offline_client  # noqa: F401


@pytest.fixture
def probe(setting_runtime):  # noqa: F811 - explicitly reused pytest fixture
    sessions, engine, store = setting_runtime
    client = _client(sessions, store)
    store.set(1, 'fictional-probe-secret')
    model = _model_payload()
    model['enabled'] = True
    setting = client.put('/paam/system/v1/setting/automation', json={
        'models': [model], 'expected_updated_time': None,
    }).json()['body']
    store.set(1, 'fictional-probe-secret')

    class Reader:
        def get_for_provider(self, model_id):
            return store.values.get(model_id)

    client.app.dependency_overrides[get_connection_secret_reader] = Reader
    yield client, setting, engine, store
    client.close()


def check(client, setting, **changes):
    return client.post('/paam/system/v1/setting/automation/model/1/connection_check', json={
        'confirmed': True, 'expected_updated_time': setting['updated_time'], **changes,
    })


def test_probe_is_one_bounded_audited_call_without_bill_reads(probe, monkeypatch, caplog):
    client, setting, engine, _ = probe
    calls, sql = [], []

    def complete(**request):
        calls.append(request)
        return {'choices': [{'message': {'content': 'raw-private-provider-result'}}]}

    monkeypatch.setattr(connection, '_direct_litellm_completion', complete)
    event.listen(engine, 'before_cursor_execute', lambda c, cur, statement, p, ctx, many: sql.append(statement))
    response = check(client, setting)
    assert response.status_code == 200
    body = response.json()['body']
    assert body['connected'] and body['mode'] == 'LIVE'
    assert body['configuration_updated_time'] == setting['updated_time']
    assert len(calls) == 1
    request = calls[0]
    assert request['messages'] == [{'role': 'user', 'content': 'Reply with OK.'}]
    assert request['max_tokens'] == 32 and request['timeout'] == 60
    assert request['proxy_url'] is None
    assert request['num_retries'] == request['max_retries'] == 0
    assert request['caching'] is False and request['stream'] is False
    assert 'provider_zero' not in request
    assert all('FROM ledger' not in statement and 'transaction_fact' not in statement.lower() for statement in sql)
    assert any('INSERT INTO llm_prompt_audit' in statement for statement in sql)
    assert any('UPDATE llm_prompt_audit' in statement for statement in sql)
    assert client.get('/paam/system/v1/setting/automation').json()['body']['updated_time'] == setting['updated_time']
    assert 'fictional-probe-secret' not in response.text + caplog.text
    assert 'raw-private-provider-result' not in response.text + caplog.text


@pytest.mark.parametrize('status,code', [(401,'AUTH_ERROR'), (403,'AUTH_ERROR'), (404,'CONFIG_ERROR'), (422,'CONFIG_ERROR'), (429,'RATE_LIMIT'), (503,'PROVIDER_UNAVAILABLE'), (None,'REQUEST_TIMEOUT')])
def test_failure_is_sanitized_and_never_retried(probe, monkeypatch, status, code):
    client, setting, _, _ = probe
    calls = []

    def fail(**request):
        calls.append(1)
        error = TimeoutError('fictional-probe-secret') if status is None else RuntimeError('fictional-probe-secret')
        error.status_code = status
        raise error

    monkeypatch.setattr(connection, '_direct_litellm_completion', fail)
    response = check(client, setting)
    assert response.status_code == 200 and response.json()['body']['code'] == code
    assert not response.json()['body']['connected']
    assert calls == [1]
    assert 'fictional-probe-secret' not in response.text


def test_confirmation_version_and_credential_gate(probe, monkeypatch):
    client, setting, _, store = probe
    monkeypatch.setattr(connection, '_direct_litellm_completion', lambda **kw: pytest.fail('must not call provider'))
    assert check(client, setting, confirmed=False).status_code == 422
    assert check(client, setting, expected_updated_time='2020-01-01T00:00:00Z').status_code == 409
    assert check(client, setting, expected_updated_time='2020-01-01T00:00:00').status_code == 422
    store.delete(1)
    assert check(client, setting).json()['body']['code'] == 'MODEL_SECRET_REQUIRED'


def test_duplicate_probe_is_rejected_until_first_finishes(probe, monkeypatch):
    client, setting, _, _ = probe
    entered, release = Event(), Event()

    def slow(**kw):
        entered.set()
        assert release.wait(5)
        return {'choices': [{'message': {'content': 'OK'}}]}

    monkeypatch.setattr(connection, '_direct_litellm_completion', slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(check, client, setting)
        try:
            assert entered.wait(5)
            assert check(client, setting).json()['body']['code'] == 'MODEL_CHECK_BUSY'
        finally:
            release.set()
        assert future.result().json()['body']['connected']
    assert check(client, setting).json()['body']['connected']


def test_actual_sdk_401_sends_one_http_request(probe, offline_client):  # noqa: F811
    client, setting, _, _ = probe
    payload = _model_payload()
    payload['enabled'] = True
    payload['litellm_params']['api_base'] = 'https://provider.invalid/v1'
    payload['litellm_params']['model'] = 'openai/pirc24-probe'
    setting = client.put('/paam/system/v1/setting/automation', json={
        'expected_updated_time': setting['updated_time'], 'models': [payload],
    }).json()['body']
    offline_client['statuses'] = [401]
    response = check(client, setting)
    assert response.json()['body']['code'] == 'AUTH_ERROR'
    assert len(offline_client['requests']) == 1
    request = json.loads(offline_client['requests'][0].content)
    assert request['max_tokens'] == 32
    assert request['messages'] == connection.MESSAGES
