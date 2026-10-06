from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator


DATABASE = Path(os.environ.get("GATEWAY_DB", "gateway.sqlite3"))
ADMIN_TOKEN = os.environ.get("GATEWAY_ADMIN_TOKEN", "")
UPSTREAM_URL = os.environ.get("GATEWAY_UPSTREAM_URL", "")
UPSTREAM_KEY = os.environ.get("GATEWAY_UPSTREAM_KEY", "")
INPUT_MICROUSD_PER_MILLION = int(os.environ.get("INPUT_MICROUSD_PER_MILLION", "150000"))
OUTPUT_MICROUSD_PER_MILLION = int(os.environ.get("OUTPUT_MICROUSD_PER_MILLION", "600000"))

app = FastAPI(title="AI Gateway", version="0.1.0")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    budget_microusd: int = Field(gt=0)


class FunctionCall(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1)
    arguments: str


class ToolCall(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1)
    type: Literal['function'] = 'function'
    function: FunctionCall


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['system', 'user', 'assistant', 'tool']
    content: str | None = None
    tool_calls: list[ToolCall] | None = Field(default=None, min_length=1)
    tool_call_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def validate_message(self):
        if self.role == 'tool' and not self.tool_call_id:
            raise ValueError('Tool messages require tool_call_id')
        if self.role != 'tool' and self.tool_call_id is not None:
            raise ValueError('tool_call_id is only valid on tool messages')
        if self.tool_calls and self.role != 'assistant':
            raise ValueError('Only assistant messages may contain tool_calls')
        if self.content is None and not (self.role == 'assistant' and self.tool_calls):
            raise ValueError('Message content is required without assistant tool calls')
        return self


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['function'] = 'function'
    function: dict[str, Any]

    @model_validator(mode='after')
    def validate_function(self):
        if not isinstance(self.function.get('name'), str) or not self.function['name'].strip():
            raise ValueError('Tool definitions require a function name')
        return self


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str = Field(min_length=1)
    messages: list[ChatMessage] = Field(min_length=1)
    max_tokens: int = Field(default=512, ge=1, le=4096)
    tools: list[ToolDefinition] | None = Field(default=None, min_length=1)
    tool_choice: Literal['auto', 'none', 'required'] | dict[str, Any] | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    response_format: dict[str, Any] | None = None
    stream: Literal[False] = False


@contextmanager
def connection():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
    finally:
        db.close()


def init_database() -> None:
    with connection() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                budget_microusd INTEGER NOT NULL,
                spent_microusd INTEGER NOT NULL DEFAULT 0,
                reserved_microusd INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY,
                client_id INTEGER NOT NULL REFERENCES clients(id),
                request_id TEXT NOT NULL DEFAULT '',
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                reserved_microusd INTEGER NOT NULL,
                cost_microusd INTEGER NOT NULL DEFAULT 0,
                response_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(client_id, idempotency_key)
            );
            """
        )
        columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
        if "request_id" not in columns:
            db.execute("ALTER TABLE runs ADD COLUMN request_id TEXT NOT NULL DEFAULT ''")
        db.commit()


@app.on_event("startup")
def startup() -> None:
    init_database()


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID")
    if not request_id or not REQUEST_ID_PATTERN.fullmatch(request_id):
        request_id = uuid.uuid4().hex
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


def require_admin(authorization: str | None) -> None:
    if not ADMIN_TOKEN or not authorization or not secrets.compare_digest(authorization, f"Bearer {ADMIN_TOKEN}"):
        raise HTTPException(401, "Invalid admin token")


def client_for_key(authorization: str | None) -> sqlite3.Row:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Client key required")
    key_hash = hashlib.sha256(authorization[7:].encode()).hexdigest()
    with connection() as db:
        client = db.execute("SELECT * FROM clients WHERE key_hash = ?", (key_hash,)).fetchone()
    if client is None:
        raise HTTPException(401, "Invalid client key")
    return client


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/admin/clients", status_code=201)
def create_client(body: ClientCreate, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(authorization)
    raw_key = f"agw_{secrets.token_urlsafe(32)}"
    with connection() as db:
        cursor = db.execute(
            "INSERT INTO clients(name,key_hash,budget_microusd) VALUES(?,?,?)",
            (body.name, hashlib.sha256(raw_key.encode()).hexdigest(), body.budget_microusd),
        )
        db.commit()
    return {"id": cursor.lastrowid, "name": body.name, "api_key": raw_key}


@app.get("/admin/clients")
def list_clients(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(authorization)
    with connection() as db:
        rows = db.execute(
            "SELECT id,name,budget_microusd,spent_microusd,reserved_microusd,created_at FROM clients ORDER BY id DESC"
        ).fetchall()
    return {"clients": [dict(row) for row in rows]}


@app.get("/admin/runs")
def list_runs(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(authorization)
    with connection() as db:
        rows = db.execute(
            "SELECT id,client_id,request_id,status,cost_microusd,error,created_at FROM runs ORDER BY id DESC LIMIT 100"
        ).fetchall()
    return {"runs": [dict(row) for row in rows]}


def estimate_reservation(body: ChatRequest) -> int:
    estimated_input = len(body.model_dump_json(exclude_none=True).encode('utf-8')) + 32 * len(body.messages)
    input_cost = estimated_input * INPUT_MICROUSD_PER_MILLION
    output_cost = body.max_tokens * OUTPUT_MICROUSD_PER_MILLION
    return max(1, (input_cost + output_cost + 999_999) // 1_000_000)


def actual_cost(usage: dict[str, Any]) -> int:
    input_tokens = int(usage["prompt_tokens"])
    output_tokens = int(usage["completion_tokens"])
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("Negative token usage")
    value = Decimal(input_tokens * INPUT_MICROUSD_PER_MILLION + output_tokens * OUTPUT_MICROUSD_PER_MILLION) / 1_000_000
    return int(value.to_integral_value(rounding=ROUND_CEILING))


async def call_upstream(body: ChatRequest) -> dict[str, Any]:
    if not UPSTREAM_URL or not UPSTREAM_KEY:
        raise RuntimeError("Upstream is not configured")
    async with httpx.AsyncClient(timeout=60) as http:
        response = await http.post(
            UPSTREAM_URL.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {UPSTREAM_KEY}"},
            json=body.model_dump(exclude_none=True),
        )
        response.raise_for_status()
        result = response.json()
    if not isinstance(result.get("usage"), dict) or not isinstance(result.get("choices"), list):
        raise RuntimeError("Upstream response lacks usage or choices")
    return result


@app.post("/v1/chat/completions")
async def chat(
    body: ChatRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
) -> dict[str, Any]:
    client = client_for_key(authorization)
    if not idempotency_key or len(idempotency_key) > 120:
        raise HTTPException(400, "Idempotency-Key is required (max 120 characters)")
    request_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    reserve = estimate_reservation(body)
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT * FROM runs WHERE client_id=? AND idempotency_key=?",
            (client["id"], idempotency_key),
        ).fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise HTTPException(409, "Idempotency key reused for a different request")
            if existing["status"] == "complete":
                return json.loads(existing["response_json"])
            raise HTTPException(409, "Request already in progress or failed")
        updated = db.execute(
            "UPDATE clients SET reserved_microusd=reserved_microusd+? "
            "WHERE id=? AND budget_microusd-spent_microusd-reserved_microusd>=?",
            (reserve, client["id"], reserve),
        )
        if updated.rowcount != 1:
            raise HTTPException(402, "Budget exhausted")
        run = db.execute(
            "INSERT INTO runs(client_id,request_id,idempotency_key,request_hash,status,reserved_microusd) VALUES(?,?,?,?,?,?)",
            (client["id"], request.state.request_id, idempotency_key, request_hash, "running", reserve),
        )
        run_id = run.lastrowid
        db.commit()

    try:
        result = await call_upstream(body)
        cost = actual_cost(result["usage"])
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        with connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE clients SET reserved_microusd=reserved_microusd-? WHERE id=?", (reserve, client["id"]))
            db.execute("UPDATE runs SET status='failed',error=? WHERE id=?", (type(exc).__name__, run_id))
            db.commit()
        raise HTTPException(502, "Upstream request failed") from exc

    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "UPDATE clients SET reserved_microusd=reserved_microusd-?,spent_microusd=spent_microusd+? WHERE id=?",
            (reserve, cost, client["id"]),
        )
        db.execute(
            "UPDATE runs SET status='complete',cost_microusd=?,response_json=? WHERE id=?",
            (cost, json.dumps(result), run_id),
        )
        db.commit()
    return result
