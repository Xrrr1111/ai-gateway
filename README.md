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

Clients call `POST /v1/chat/completions` with `Authorization: Bearer <issued-key>` and `Idempotency-Key: <unique-request-id>`. Every response includes an `X-Request-ID`; clients may provide a short trace-safe value or let the gateway generate one. The ID is stored with the run record and is safe to use when correlating support logs. The upstream key never goes to the browser. Run `pytest` from this directory.

## Agent compatibility

The non-streaming endpoint forwards native function-tool definitions, `tool_choice`, assistant tool calls, tool observations with `tool_call_id`, `temperature`, and `response_format`. Assistant messages may omit text when carrying tool calls. Unsupported top-level options and streaming requests fail validation instead of silently losing controls. Budget estimates include serialized messages, tool schemas and output constraints; these estimates are not strict provider billing limits.

The portfolio's [customer-support Agent](https://github.com/Xrrr1111/enterprise-support-agent) uses native tool calling, while the [data-analysis Agent](https://github.com/Xrrr1111/data-analyst-agent) uses JSON decisions and read-only tools. Both adapters issue a separate idempotency key per decision and request at most 2048 output tokens. Keys do not persist across process restarts or a new adapter invocation; this does not guarantee workflow-wide exactly-once execution.

## Current limits

This is a local prototype, **not** a deployed production service. Price configuration is manual; the upstream must supply trustworthy token usage. Budget reservations are estimates and actual usage can exceed the remaining balance. SQLite is suitable for one process, not a distributed deployment. Missing pieces include secret rotation, authenticated staff accounts, durable distributed rate limits, monitoring/alerts, backups, load testing, and a live operating record. Do not describe it as production until those are implemented and verified in actual use.
