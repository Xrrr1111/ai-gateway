# AI Gateway

A single-tenant, OpenAI-compatible request gateway and admin console. It issues client keys, checks an estimated budget before upstream calls, records measured token costs, and deduplicates retried requests.

## Run locally

Set `GATEWAY_ADMIN_TOKEN` to a random secret. Set `GATEWAY_UPSTREAM_URL` to an OpenAI-compatible `/v1` base URL and `GATEWAY_UPSTREAM_KEY` to its key. Set `GATEWAY_DB` for a persistent SQLite file. Configure `INPUT_MICROUSD_PER_MILLION` and `OUTPUT_MICROUSD_PER_MILLION` to match the upstream model price before using budgets.

```powershell
pip install -r requirements.txt
$env:GATEWAY_ADMIN_TOKEN = "replace-with-random-secret"
$env:GATEWAY_UPSTREAM_URL = "https://provider.example/v1"
$env:GATEWAY_UPSTREAM_KEY = "replace-with-provider-key"
uvicorn backend.app:app --host 127.0.0.1 --port 8100
```

In another terminal, `cd frontend`, run `npm install`, then `npm run dev`. The console opens at `http://127.0.0.1:5173` or the next free Vite port. The browser holds the admin token in memory only; refresh clears it.

Clients call `POST /v1/chat/completions` with `Authorization: Bearer <issued-key>` and `Idempotency-Key: <unique-request-id>`. The upstream key never goes to the browser. Run `pytest` from this directory.

## Current limits

This is a local prototype, **not** a deployed production service. Price configuration is manual; the upstream must supply trustworthy token usage. Budget reservations are estimates and actual usage can exceed the remaining balance. SQLite is suitable for one process, not a distributed deployment. Missing pieces include secret rotation, authenticated staff accounts, durable distributed rate limits, monitoring/alerts, backups, load testing, and a live operating record. Do not describe it as production until those are implemented and verified in actual use.
