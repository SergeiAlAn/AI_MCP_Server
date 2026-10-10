"""product-mcp — MCP-сервер для работы с базой товаров.

Протокол: MCP (JSON-RPC 2.0) поверх HTTP, эндпоинт POST /mcp.
Поддерживаемые методы: initialize, notifications/initialized, ping, tools/list, tools/call.

Дополнительно доступны упрощённые REST-эндпоинты:
    GET  /health              — проверка работоспособности
    GET  /tools               — список инструментов в формате MCP JSON Schema
    POST /tools/{tool_name}   — вызов инструмента, тело запроса — JSON с аргументами

Запуск: python server.py
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

import db
import tools

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")

SERVER_NAME = "product-mcp"
SERVER_VERSION = "1.0.0"
SUPPORTED_PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]

HOST = os.getenv("MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("MCP_PORT", "8000"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(SERVER_NAME)

@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    logger.info("База данных готова: %s", db.DB_PATH)
    yield


app = FastAPI(title=SERVER_NAME, version=SERVER_VERSION, lifespan=lifespan)


# ---------------------------------------------------------------------------
# JSON-RPC / MCP
# ---------------------------------------------------------------------------

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _rpc_result(request_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_call_result(name: str, arguments: dict) -> dict:
    try:
        data = tools.call_tool(name, arguments)
    except tools.ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
        "structuredContent": data,
        "isError": False,
    }


def _handle_rpc_message(message: Any) -> dict | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
        return _rpc_error(None, INVALID_REQUEST, "Invalid Request")

    method = message["method"]
    params = message.get("params") or {}
    request_id = message.get("id")
    is_notification = "id" not in message

    if is_notification:
        logger.info("Notification: %s", method)
        return None

    try:
        if method == "initialize":
            client_version = params.get("protocolVersion")
            version = (
                client_version
                if client_version in SUPPORTED_PROTOCOL_VERSIONS
                else SUPPORTED_PROTOCOL_VERSIONS[0]
            )
            return _rpc_result(
                request_id,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    "instructions": (
                        "Сервер для работы с базой товаров: просмотр, поиск, добавление, аналитика цен, "
                        "калькулятор, курсы валют ЦБ РФ и криптовалют CoinGecko. Цены в базе — в рублях."
                    ),
                },
            )

        if method == "ping":
            return _rpc_result(request_id, {})

        if method == "tools/list":
            return _rpc_result(request_id, {"tools": tools.get_tool_definitions()})

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name not in tools.TOOLS:
                return _rpc_error(request_id, INVALID_PARAMS, f"Unknown tool: {name}")
            if not isinstance(arguments, dict):
                return _rpc_error(request_id, INVALID_PARAMS, "'arguments' must be an object")
            logger.info("tools/call %s %s", name, arguments)
            return _rpc_result(request_id, _tool_call_result(name, arguments))

        return _rpc_error(request_id, METHOD_NOT_FOUND, f"Method not found: {method}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка обработки %s", method)
        return _rpc_error(request_id, INTERNAL_ERROR, str(exc))


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> Response:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(_rpc_error(None, PARSE_ERROR, "Parse error"))

    if isinstance(payload, list):
        responses = [r for r in [await run_in_threadpool(_handle_rpc_message, m) for m in payload] if r is not None]
        return JSONResponse(responses) if responses else Response(status_code=202)

    response = await run_in_threadpool(_handle_rpc_message, payload)
    if response is None:
        return Response(status_code=202)
    return JSONResponse(response)


@app.get("/mcp")
async def mcp_get() -> Response:
    # Сервер не отправляет уведомления по SSE-потоку.
    return Response(status_code=405, headers={"Allow": "POST"})


# ---------------------------------------------------------------------------
# Упрощённые REST-эндпоинты
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "server": SERVER_NAME, "version": SERVER_VERSION}


@app.get("/tools")
async def list_tools() -> dict:
    return {"tools": tools.get_tool_definitions()}


@app.post("/tools/{tool_name}")
async def call_tool_rest(tool_name: str, request: Request) -> JSONResponse:
    if tool_name not in tools.TOOLS:
        return JSONResponse({"error": f"Unknown tool: {tool_name}"}, status_code=404)
    body = await request.body()
    try:
        arguments = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return JSONResponse({"error": "Тело запроса должно быть JSON-объектом"}, status_code=400)
    if not isinstance(arguments, dict):
        return JSONResponse({"error": "Тело запроса должно быть JSON-объектом"}, status_code=400)
    result = await run_in_threadpool(_tool_call_result, tool_name, arguments)
    return JSONResponse(result, status_code=400 if result["isError"] else 200)


if __name__ == "__main__":
    logger.info("Запуск %s на http://%s:%s (MCP endpoint: /mcp)", SERVER_NAME, HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT)
