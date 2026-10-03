import asyncio

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
