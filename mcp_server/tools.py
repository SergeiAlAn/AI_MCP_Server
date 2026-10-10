"""MCP-инструменты сервера product-mcp и их описание в формате MCP JSON Schema."""

import ast
import math
import operator
from typing import Any, Callable

import currency
import db


class ToolError(Exception):
    """Ошибка выполнения инструмента (возвращается клиенту с isError=true)."""


# ---------------------------------------------------------------------------
# Безопасный калькулятор на основе AST (без eval)
# ---------------------------------------------------------------------------

_BIN_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS: dict[type, Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "pow": math.pow,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
}

_CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e}

_MAX_EXPRESSION_LENGTH = 200
_MAX_POWER_EXPONENT = 1000
_MAX_ABS_RESULT = 1e100


def _eval_node(node: ast.AST) -> float | int:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ToolError("Допускаются только числа")
        return node.value

    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POWER_EXPONENT:
            raise ToolError("Слишком большая степень")
        result = _BIN_OPS[type(node.op)](left, right)
        if isinstance(result, complex):
            raise ToolError("Результат не является вещественным числом")
        if abs(result) > _MAX_ABS_RESULT:
            raise ToolError("Слишком большое число")
        return result

    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))

    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]

    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCTIONS
        and not node.keywords
    ):
        args = [_eval_node(arg) for arg in node.args]
        return _FUNCTIONS[node.func.id](*args)

    raise ToolError(f"Недопустимый элемент выражения: {type(node).__name__}")


def safe_calculate(expression: str) -> float | int:
    expression = expression.strip().replace("^", "**").replace("×", "*").replace("÷", "/")
    if not expression:
        raise ToolError("Пустое выражение")
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise ToolError("Выражение слишком длинное")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"Синтаксическая ошибка в выражении: {expression}") from exc
    try:
        result = _eval_node(tree)
    except ZeroDivisionError as exc:
        raise ToolError("Деление на ноль") from exc
    except (ValueError, TypeError, OverflowError) as exc:
        raise ToolError(f"Ошибка вычисления: {exc}") from exc
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        return int(result)
    if isinstance(result, float):
        return round(result, 10)
    return result


# ---------------------------------------------------------------------------
# Реализация инструментов
# ---------------------------------------------------------------------------

def _require_str(arguments: dict, key: str) -> str:
    value = arguments.get(key)
    if value is None or not str(value).strip():
        raise ToolError(f"Параметр '{key}' обязателен")
    return str(value).strip()


def _parse_number(value: Any, key: str) -> float:
    try:
        number = float(str(value).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError) as exc:
        raise ToolError(f"Параметр '{key}' должен быть числом") from exc
    if not math.isfinite(number):
        raise ToolError(f"Параметр '{key}' должен быть конечным числом")
    return number


def _str_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        raise ToolError("Ожидается список строк")
    return [str(item).strip() for item in value if str(item).strip()]


def _with_currency(result: dict, arguments: dict) -> dict:
    """Добавляет к товарам цену в валюте arguments["currency"] (цены в базе — в рублях)."""
    code = str(arguments.get("currency") or "").strip()
    if not code:
        return result
    rate = currency.get_rate(code)
    if rate["code"] == "RUB":
        return result
    for product in result.get("products", []):
        product["price_converted"] = currency.round_amount(product["price"] / rate["rub_per_unit"], rate["type"])
    result["currency"] = rate
    return result


def list_products(arguments: dict) -> dict:
    products = db.get_all_products()
    return _with_currency({"count": len(products), "products": products}, arguments)


def find_product(arguments: dict) -> dict:
    name = _require_str(arguments, "name")
    products = db.search_products_by_name(name)
    return _with_currency({"query": name, "count": len(products), "products": products}, arguments)


def find_products_by_category(arguments: dict) -> dict:
    category = _require_str(arguments, "category")
    products = db.search_products_by_category(category)
    return _with_currency({"category": category, "count": len(products), "products": products}, arguments)


_ORDER_ALIASES = {
    "expensive": "expensive", "desc": "expensive", "max": "expensive", "дорогие": "expensive",
    "cheap": "cheap", "asc": "cheap", "min": "cheap", "дешевые": "cheap", "дешёвые": "cheap",
}
_MAX_TOP_LIMIT = 50


def top_products(arguments: dict) -> dict:
    order = _ORDER_ALIASES.get(str(arguments.get("order", "expensive")).strip().lower())
    if order is None:
        raise ToolError("Параметр 'order' должен быть 'expensive' или 'cheap'")
    limit = int(_parse_number(arguments.get("limit", 5), "limit"))
    if not 1 <= limit <= _MAX_TOP_LIMIT:
        raise ToolError(f"Параметр 'limit' должен быть от 1 до {_MAX_TOP_LIMIT}")
    categories = _str_list(arguments.get("categories"))
    per_category = bool(arguments.get("per_category", False))

    products = db.search_products_by_categories(categories)
    if not products:
        return _with_currency(
            {"order": order, "limit": limit, "categories": categories, "per_category": per_category,
             "count": 0, "products": [], "groups": []},
            arguments,
        )

    reverse = order == "expensive"
    ranked = sorted(products, key=lambda p: (p["price"], -p["id"]), reverse=reverse)

    groups: list[dict] = []
    if per_category:
        by_category: dict[str, list[dict]] = {}
        for product in ranked:
            by_category.setdefault(product["category"] or "Без категории", []).append(product)
        for category_name in sorted(by_category):
            groups.append({"category": category_name, "products": by_category[category_name][:limit]})
        selected = [p for g in groups for p in g["products"]]
    else:
        selected = ranked[:limit]

    prices = [p["price"] for p in products]
    result = {
        "order": order,
        "limit": limit,
        "categories": categories,
        "per_category": per_category,
        "count": len(selected),
        "total_considered": len(products),
        "stats": {
            "min_price": min(prices),
            "max_price": max(prices),
            "avg_price": round(sum(prices) / len(prices), 2),
        },
        "products": selected,
        "groups": groups,
    }
    return _with_currency(result, arguments)


def convert_currency(arguments: dict) -> dict:
    amount = _parse_number(arguments.get("amount"), "amount")
    to_currency = _require_str(arguments, "to_currency")
    from_currency = str(arguments.get("from_currency") or "RUB")
    return currency.convert(amount, from_currency, to_currency)


def get_exchange_rates(arguments: dict) -> dict:
    data = currency.get_cbr_rates(arguments.get("date"))
    requested = [currency.normalize_currency(c) for c in _str_list(arguments.get("currencies"))]
    codes = requested or [c for c in currency.POPULAR_FIAT if c in data["rates"]]
    missing = [c for c in codes if c not in data["rates"]]
    rates = [data["rates"][c] for c in codes if c in data["rates"]]
    return {"date": data["date"], "source": data["source"], "rates": rates, "missing": missing}


def get_crypto_prices(arguments: dict) -> dict:
    coins = _str_list(arguments.get("coins")) or ["BTC", "ETH"]
    vs = _str_list(arguments.get("currencies")) or ["RUB", "USD"]
    coin_codes = [currency.normalize_currency(c) for c in coins]
    coin_ids = [currency.CRYPTO_IDS.get(c, c.lower()) for c in coin_codes]
    vs_codes = [currency.normalize_currency(v).lower() for v in vs]
    data = currency.get_crypto_prices(coin_ids, vs_codes)
    prices = []
    for code, coin_id in zip(coin_codes, coin_ids):
        if coin_id not in data:
            continue
        prices.append({
            "coin": code.upper() if code in currency.CRYPTO_IDS else coin_id,
            "id": coin_id,
            "prices": {v.upper(): data[coin_id].get(v) for v in vs_codes if data[coin_id].get(v) is not None},
        })
    missing = [c for c, i in zip(coin_codes, coin_ids) if i not in data]
    return {"source": "CoinGecko", "prices": prices, "missing": missing}


def add_product(arguments: dict) -> dict:
    name = _require_str(arguments, "name")
    category = _require_str(arguments, "category")
    price = _parse_number(arguments.get("price"), "price")
    if price < 0:
        raise ToolError("Цена должна быть неотрицательным числом")
    result: dict = {"message": "Товар успешно добавлен"}
    code = str(arguments.get("currency") or "").strip()
    if code:
        conversion = currency.convert(price, code, "RUB")
        if conversion["from"]["code"] != "RUB":
            result["original_price"] = {"amount": price, "currency": conversion["from"]}
            price = conversion["rub_amount"]
    result["product"] = db.insert_product(name, category, round(price, 2))
    return result


def calculate(arguments: dict) -> dict:
    expression = _require_str(arguments, "expression")
    return {"expression": expression, "result": safe_calculate(expression)}


# ---------------------------------------------------------------------------
# Реестр инструментов в формате MCP (name, description, inputSchema)
# ---------------------------------------------------------------------------

_CURRENCY_PROPERTY = {
    "type": "string",
    "description": (
        "Необязательно. Валюта, в которой дополнительно показать цены (цены в базе — в рублях): "
        "код ЦБ РФ (USD, EUR, CNY...) или криптовалюты (BTC, ETH, USDT, TON...)."
    ),
}

TOOLS: dict[str, dict[str, Any]] = {
    "list_products": {
        "handler": list_products,
        "definition": {
            "name": "list_products",
            "title": "Список товаров",
            "description": "Возвращает список всех товаров из базы данных.",
            "inputSchema": {
                "type": "object",
                "properties": {"currency": _CURRENCY_PROPERTY},
                "additionalProperties": False,
            },
        },
    },
    "find_product": {
        "handler": find_product,
        "definition": {
            "name": "find_product",
            "title": "Поиск товара по имени",
            "description": "Ищет товары, в названии которых встречается подстрока (без учёта регистра).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Часть названия товара"},
                    "currency": _CURRENCY_PROPERTY,
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    "find_products_by_category": {
        "handler": find_products_by_category,
        "definition": {
            "name": "find_products_by_category",
            "title": "Поиск товаров по категории",
            "description": "Возвращает товары указанной категории (без учёта регистра, допускается часть названия).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Название категории"},
                    "currency": _CURRENCY_PROPERTY,
                },
                "required": ["category"],
                "additionalProperties": False,
            },
        },
    },
    "add_product": {
        "handler": add_product,
        "definition": {
            "name": "add_product",
            "title": "Добавить товар",
            "description": (
                "Добавляет новый товар в базу данных. Если цена указана не в рублях, "
                "она конвертируется в рубли по текущему курсу."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Название товара"},
                    "category": {"type": "string", "description": "Категория товара"},
                    "price": {"type": "number", "minimum": 0, "description": "Цена товара"},
                    "currency": {
                        "type": "string",
                        "description": "Необязательно. Валюта цены (по умолчанию RUB): USD, EUR, BTC...",
                    },
                },
                "required": ["name", "category", "price"],
                "additionalProperties": False,
            },
        },
    },
    "calculate": {
        "handler": calculate,
        "definition": {
            "name": "calculate",
            "title": "Калькулятор",
            "description": (
                "Безопасно вычисляет математическое выражение. Поддерживаются + - * / // % ** ^, "
                "скобки, константы pi и e, функции abs, round, min, max, sqrt, pow, sin, cos, tan, "
                "log, log10, exp, floor, ceil."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Математическое выражение, например (2+3)*4"}
                },
                "required": ["expression"],
                "additionalProperties": False,
            },
        },
    },
    "top_products": {
        "handler": top_products,
        "definition": {
            "name": "top_products",
            "title": "Самые дорогие / дешёвые товары",
            "description": (
                "Возвращает N самых дорогих или самых дешёвых товаров из всего списка, из одной или "
                "нескольких категорий, либо топ-N в каждой категории отдельно. Также возвращает "
                "минимальную, максимальную и среднюю цену по выборке."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "order": {
                        "type": "string",
                        "enum": ["expensive", "cheap"],
                        "description": "expensive — самые дорогие, cheap — самые дешёвые",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _MAX_TOP_LIMIT,
                        "default": 5,
                        "description": "Сколько товаров вернуть (в каждой категории, если per_category=true)",
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Необязательно. Список категорий; если пусто — по всем товарам",
                    },
                    "per_category": {
                        "type": "boolean",
                        "default": False,
                        "description": "true — отдельный топ для каждой категории",
                    },
                    "currency": _CURRENCY_PROPERTY,
                },
                "required": ["order"],
                "additionalProperties": False,
            },
        },
    },
    "convert_currency": {
        "handler": convert_currency,
        "definition": {
            "name": "convert_currency",
            "title": "Конвертер валют",
            "description": (
                "Конвертирует сумму между рублём, валютами ЦБ РФ и криптовалютами (курс ЦБ РФ и CoinGecko)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "Сумма"},
                    "from_currency": {"type": "string", "default": "RUB", "description": "Исходная валюта"},
                    "to_currency": {"type": "string", "description": "Целевая валюта"},
                },
                "required": ["amount", "to_currency"],
                "additionalProperties": False,
            },
        },
    },
    "get_exchange_rates": {
        "handler": get_exchange_rates,
        "definition": {
            "name": "get_exchange_rates",
            "title": "Курсы валют ЦБ РФ",
            "description": "Официальные курсы валют ЦБ РФ к рублю на сегодня или на указанную дату.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "currencies": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Необязательно. Коды валют (USD, EUR...); если пусто — популярные валюты",
                    },
                    "date": {"type": "string", "description": "Необязательно. Дата в формате ДД.ММ.ГГГГ"},
                },
                "additionalProperties": False,
            },
        },
    },
    "get_crypto_prices": {
        "handler": get_crypto_prices,
        "definition": {
            "name": "get_crypto_prices",
            "title": "Цены криптовалют",
            "description": "Текущие цены криптовалют по данным CoinGecko.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "coins": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Монеты: BTC, ETH, USDT, TON, SOL... или id CoinGecko; по умолчанию BTC и ETH",
                    },
                    "currencies": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "В каких валютах показать цену; по умолчанию RUB и USD",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
}


def get_tool_definitions() -> list[dict]:
    return [tool["definition"] for tool in TOOLS.values()]


def call_tool(name: str, arguments: dict | None) -> dict:
    tool = TOOLS.get(name)
    if tool is None:
        raise KeyError(name)
    try:
        return tool["handler"](arguments or {})
    except currency.CurrencyError as exc:
        raise ToolError(str(exc)) from exc
