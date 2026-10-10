"""HTTP-клиент для MCP-сервера product-mcp (JSON-RPC 2.0, эндпоинт /mcp)."""

import itertools
import json
from typing import Any

import httpx

from config import MCP_SERVER_URL

PROTOCOL_VERSION = "2025-06-18"


class MCPError(Exception):
    """Ошибка протокола или выполнения инструмента на стороне MCP-сервера."""


class MCPClient:
    def __init__(self, url: str = MCP_SERVER_URL, timeout: float = 30.0) -> None:
        self.url = url
        self._http = httpx.AsyncClient(
            timeout=timeout,
            headers={"Accept": "application/json, text/event-stream"},
        )
        self._ids = itertools.count(1)
        self._initialized = False

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, params: dict | None = None) -> Any:
        payload = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
        if params is not None:
            payload["params"] = params
        try:
            response = await self._http.post(self.url, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MCPError(f"MCP-сервер недоступен ({self.url}): {exc}") from exc
        data = response.json()
        if "error" in data:
            raise MCPError(data["error"].get("message", "Неизвестная ошибка MCP"))
        return data.get("result")

    async def _notify(self, method: str) -> None:
        try:
            await self._http.post(self.url, json={"jsonrpc": "2.0", "method": method})
        except httpx.HTTPError as exc:
            raise MCPError(f"MCP-сервер недоступен ({self.url}): {exc}") from exc

    async def initialize(self) -> dict:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "product-telegram-bot", "version": "1.0.0"},
            },
        )
        await self._notify("notifications/initialized")
        self._initialized = True
        return result

    async def _ensure_initialized(self) -> None:
        if not self._initialized:
            await self.initialize()

    async def list_tools(self) -> list[dict]:
        await self._ensure_initialized()
        result = await self._request("tools/list")
        return result.get("tools", [])

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Вызывает инструмент и возвращает его структурированный результат."""
        await self._ensure_initialized()
        result = await self._request("tools/call", {"name": name, "arguments": arguments or {}})
        text = "\n".join(
            item.get("text", "") for item in result.get("content", []) if item.get("type") == "text"
        )
        if result.get("isError"):
            raise MCPError(text or "Инструмент вернул ошибку")
        if "structuredContent" in result:
            return result["structuredContent"]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    # Удобные обёртки над инструментами сервера

    @staticmethod
    def _with_optional(arguments: dict, **optional: Any) -> dict:
        arguments.update({k: v for k, v in optional.items() if v not in (None, "", [])})
        return arguments

    async def list_products(self, currency: str | None = None) -> dict:
        return await self.call_tool("list_products", self._with_optional({}, currency=currency))

    async def find_product(self, name: str, currency: str | None = None) -> dict:
        return await self.call_tool("find_product", self._with_optional({"name": name}, currency=currency))

    async def find_products_by_category(self, category: str, currency: str | None = None) -> dict:
        return await self.call_tool(
            "find_products_by_category", self._with_optional({"category": category}, currency=currency)
        )

    async def add_product(self, name: str, category: str, price: float, currency: str | None = None) -> dict:
        return await self.call_tool(
            "add_product",
            self._with_optional({"name": name, "category": category, "price": price}, currency=currency),
        )

    async def calculate(self, expression: str) -> dict:
        return await self.call_tool("calculate", {"expression": expression})

    async def top_products(
        self,
        order: str = "expensive",
        limit: int = 5,
        categories: list[str] | None = None,
        per_category: bool = False,
        currency: str | None = None,
    ) -> dict:
        arguments = {"order": order, "limit": limit, "per_category": per_category}
        return await self.call_tool(
            "top_products", self._with_optional(arguments, categories=categories, currency=currency)
        )

    async def convert_currency(self, amount: float, to_currency: str, from_currency: str = "RUB") -> dict:
        return await self.call_tool(
            "convert_currency", {"amount": amount, "from_currency": from_currency, "to_currency": to_currency}
        )

    async def get_exchange_rates(self, currencies: list[str] | None = None, date: str | None = None) -> dict:
        return await self.call_tool("get_exchange_rates", self._with_optional({}, currencies=currencies, date=date))

    async def get_crypto_prices(self, coins: list[str] | None = None, currencies: list[str] | None = None) -> dict:
        return await self.call_tool("get_crypto_prices", self._with_optional({}, coins=coins, currencies=currencies))
