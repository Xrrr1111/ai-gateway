import asyncio
import json

import httpx
import pytest

from fastapi.testclient import TestClient

from backend import app as gateway


def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "DATABASE", tmp_path / "gateway.sqlite3")
    monkeypatch.setattr(gateway, "ADMIN_TOKEN", "admin-secret")
    gateway.init_database()
    return TestClient(gateway.app)


def issue_key(client, budget=1000):
    response = client.post(
        "/admin/clients",
        headers={"Authorization": "Bearer admin-secret"},
        json={"name": "support-service", "budget_microusd": budget},
    )
    assert response.status_code == 201
    return response.json()["api_key"]


def request(client, key, idem="task-1", message="hello"):
    return client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Idempotency-Key": idem},
        json={"model": "test", "messages": [{"role": "user", "content": message}], "max_tokens": 20},
    )


def test_auth_budget_and_idempotency(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    assert client.get("/admin/clients").status_code == 401
    key = issue_key(client)
    calls = []

    async def upstream(body):
        calls.append(body)
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}

    monkeypatch.setattr(gateway, "call_upstream", upstream)
    first = request(client, key)
    assert first.status_code == 200
    assert request(client, key).json() == first.json()
    assert len(calls) == 1
    assert request(client, key, message="different").status_code == 409
    row = client.get("/admin/clients", headers={"Authorization": "Bearer admin-secret"}).json()["clients"][0]
    assert row["spent_microusd"] > 0
    assert row["reserved_microusd"] == 0
    assert "key_hash" not in row


def test_failure_refunds_reservation(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    key = issue_key(client)

    async def upstream(body):
        raise RuntimeError("provider offline")

    monkeypatch.setattr(gateway, "call_upstream", upstream)
    assert request(client, key).status_code == 502
    row = client.get("/admin/clients", headers={"Authorization": "Bearer admin-secret"}).json()["clients"][0]
    assert row["reserved_microusd"] == 0
    assert row["spent_microusd"] == 0


def test_insufficient_budget_does_not_call_provider(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    key = issue_key(client, budget=1)

    async def upstream(body):
        raise AssertionError("provider should not be called")

    monkeypatch.setattr(gateway, "call_upstream", upstream)
    assert request(client, key).status_code == 402


def test_agent_payload_reaches_upstream_without_losing_controls(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    key = issue_key(client, budget=1000000)
    received = []

    def provider(request):
        received.append(json.loads(request.content))
        assert request.headers['authorization'] == 'Bearer provider-test-key'
        return httpx.Response(200, json={'choices': [{'message': {'content': 'done'}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 3}})

    async_client = httpx.AsyncClient
    monkeypatch.setattr(gateway, 'UPSTREAM_URL', 'https://model.example/v1')
    monkeypatch.setattr(gateway, 'UPSTREAM_KEY', 'provider-test-key')
    monkeypatch.setattr(gateway.httpx, 'AsyncClient', lambda **kwargs: async_client(transport=httpx.MockTransport(provider), **kwargs))
    payload = {
        'model': 'test', 'max_tokens': 32, 'temperature': 0,
        'response_format': {'type': 'json_object'},
        'tools': [{'type': 'function', 'function': {'name': 'query_order', 'parameters': {'type': 'object'}}}],
        'tool_choice': 'auto',
        'messages': [
            {'role': 'user', 'content': 'Where is ORD-1002?'},
            {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call-1', 'type': 'function', 'function': {'name': 'query_order', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'call-1', 'content': '{"status":"pending"}'},
        ],
    }
    headers = {'Authorization': f'Bearer {key}', 'Idempotency-Key': 'agent-step'}
    first = client.post('/v1/chat/completions', headers=headers, json=payload)
    assert first.status_code == 200, first.text
    assert client.post('/v1/chat/completions', headers=headers, json=payload).json() == first.json()
    assert len(received) == 1
    for field in ['tools', 'tool_choice', 'response_format', 'temperature']:
        assert received[0][field] == payload[field]
    assert received[0]['messages'][1]['tool_calls'] == payload['messages'][1]['tool_calls']
    assert received[0]['messages'][2] == payload['messages'][2]
    payload['tools'][0]['function']['name'] = 'different_tool'
    assert client.post('/v1/chat/completions', headers=headers, json=payload).status_code == 409


@pytest.mark.parametrize('changes', [
    {'stream': True},
    {'unknown_generation_option': 1},
    {'messages': [{'role': 'tool', 'content': 'missing call id'}]},
    {'messages': [{'role': 'user', 'content': None}]},
])
def test_unsupported_or_incomplete_agent_requests_fail_before_provider(tmp_path, monkeypatch, changes):
    client = make_client(tmp_path, monkeypatch)
    key = issue_key(client)
    payload = {'model': 'test', 'messages': [{'role': 'user', 'content': 'hello'}], **changes}
    response = client.post('/v1/chat/completions', headers={'Authorization': f'Bearer {key}', 'Idempotency-Key': 'invalid'}, json=payload)
    assert response.status_code == 422
    with gateway.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM runs').fetchone()[0] == 0
