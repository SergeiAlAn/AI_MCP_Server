"""Telegram-бот для работы с базой товаров через MCP-сервер product-mcp и LLM (ProxyAPI)."""

import html
import json
import logging
import re
from collections import defaultdict

from openai import AsyncOpenAI, OpenAIError
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from mcp_client import MCPClient, MCPError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("product-bot")

TELEGRAM_MESSAGE_LIMIT = 4000
KNOWN_TOOLS = {
    "list_products",
    "find_product",
    "find_products_by_category",
    "add_product",
    "calculate",
    "top_products",
    "convert_currency",
    "get_exchange_rates",
    "get_crypto_prices",
}
CURRENCY_SYMBOLS = {"RUB": "₽", "USD": "$", "EUR": "€", "CNY": "¥", "GBP": "£", "JPY": "¥"}

SYSTEM_PROMPT = config.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

llm = AsyncOpenAI(api_key=config.ProxyAPI_KEY, base_url=config.ProxyAPI_BASE_URL, timeout=90)
mcp = MCPClient(config.MCP_SERVER_URL)


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

async def ask_llm(history: list[dict], user_text: str) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history, {"role": "user", "content": user_text}]
    response = await llm.chat.completions.create(model=config.LLM_MODEL, messages=messages)
    return (response.choices[0].message.content or "").strip()


def extract_tool_call(llm_reply: str) -> dict | None:
    """Возвращает {"tool": ..., "arguments": {...}}, если ответ LLM — вызов инструмента."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", llm_reply.strip(), flags=re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("tool") not in KNOWN_TOOLS:
        return None
    arguments = data.get("arguments")
    return {"tool": data["tool"], "arguments": arguments if isinstance(arguments, dict) else {}}


# ---------------------------------------------------------------------------
# Форматирование ответов
# ---------------------------------------------------------------------------

def fmt_number(value: float, decimals: int = 2, strip_zeros: bool = False) -> str:
    if value and abs(value) < 0.01 and decimals <= 2:
        decimals, strip_zeros = 6, True
    text = f"{value:,.{decimals}f}".replace(",", " ")
    if "." in text:
        integer, fraction = text.split(".")
        if not fraction.strip("0"):
            return integer
        if strip_zeros:
            text = text.rstrip("0")
    return text


def fmt_money(value: float, code: str = "RUB", currency_type: str = "fiat") -> str:
    code = code.upper()
    if currency_type == "crypto":
        decimals = 8 if abs(value) < 1 else 4
        return f"{fmt_number(value, decimals, strip_zeros=True)} {code}"
    symbol = CURRENCY_SYMBOLS.get(code, code)
    return f"{fmt_number(value)} {symbol}"


def fmt_price(price: float) -> str:
    return fmt_money(price, "RUB")


def fmt_price_with_currency(rub_price: float, rate: dict | None, converted: float | None = None) -> str:
    text = f"<b>{fmt_price(rub_price)}</b>"
    if rate:
        if converted is None:
            converted = rub_price / rate["rub_per_unit"]
        text += f" (≈ {fmt_money(converted, rate['code'], rate['type'])})"
    return text


def fmt_rate_note(rate: dict | None) -> str:
    if not rate or rate["code"] == "RUB":
        return ""
    when = f", {rate['date']}" if rate.get("date") else ""
    return (
        f"\n\n💱 <i>Курс: 1 {rate['code']} = {fmt_price(rate['rub_per_unit'])} "
        f"({html.escape(rate['source'])}{when})</i>"
    )


def fmt_product_line(product: dict, rate: dict | None = None, prefix: str = "•") -> str:
    return (
        f"{prefix} <code>#{product['id']}</code> {html.escape(product['name'])} — "
        f"{fmt_price_with_currency(product['price'], rate, product.get('price_converted'))}"
    )


def fmt_products_grouped(products: list[dict], rate: dict | None = None) -> str:
    groups: dict[str, list[dict]] = defaultdict(list)
    for product in products:
        groups[product["category"] or "Без категории"].append(product)
    blocks = []
    for category in sorted(groups):
        lines = [f"\n🏷 <b>{html.escape(category)}</b> ({len(groups[category])})"]
        lines += [fmt_product_line(p, rate) for p in groups[category]]
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


RANK_MARKS = {1: "🥇", 2: "🥈", 3: "🥉"}


def fmt_ranked(products: list[dict], rate: dict | None, show_category: bool = True) -> str:
    lines = []
    for position, p in enumerate(products, start=1):
        mark = RANK_MARKS.get(position, f"{position}.")
        category = f" · <i>{html.escape(p['category'])}</i>" if show_category else ""
        lines.append(
            f"{mark} {html.escape(p['name'])} — "
            f"{fmt_price_with_currency(p['price'], rate, p.get('price_converted'))}{category}"
        )
    return "\n".join(lines)


def format_top_products(result: dict) -> str:
    rate = result.get("currency")
    expensive = result["order"] == "expensive"
    title = "💎 Самые дорогие товары" if expensive else "🏷 Самые дешёвые товары"
    categories = result.get("categories") or []
    scope = (
        "в категориях: " + ", ".join(html.escape(c) for c in categories) if categories else "среди всех товаров"
    )
    if not result["count"]:
        return f"🔍 Товаров {scope} не найдено. Проверьте название категории."

    if result.get("per_category"):
        header = f"{title} — топ-{result['limit']} в каждой категории ({scope})"
        body = "\n\n".join(
            f"📂 <b>{html.escape(g['category'])}</b>\n{fmt_ranked(g['products'], rate, show_category=False)}"
            for g in result["groups"]
        )
    else:
        header = f"{title} — топ-{result['count']} {scope}"
        body = fmt_ranked(result["products"], rate)

    stats = result["stats"]
    stats_text = (
        f"\n\n📊 <b>Статистика по выборке</b> ({result['total_considered']} шт.):\n"
        f"• минимум: {fmt_price_with_currency(stats['min_price'], rate)}\n"
        f"• максимум: {fmt_price_with_currency(stats['max_price'], rate)}\n"
        f"• средняя цена: {fmt_price_with_currency(stats['avg_price'], rate)}"
    )
    return f"<b>{header}</b>\n\n{body}{stats_text}{fmt_rate_note(rate)}"


def format_conversion(result: dict) -> str:
    source, target = result["from"], result["to"]
    lines = [
        "💱 <b>Конвертация валют</b>\n",
        f"{fmt_money(result['amount'], source['code'], source['type'])} = "
        f"<b>{fmt_money(result['result'], target['code'], target['type'])}</b>",
    ]
    for rate in (source, target):
        if rate["code"] != "RUB":
            when = f", {rate['date']}" if rate.get("date") else ""
            lines.append(
                f"<i>1 {rate['code']} = {fmt_price(rate['rub_per_unit'])} ({html.escape(rate['source'])}{when})</i>"
            )
    return "\n".join(lines)


def format_exchange_rates(result: dict) -> str:
    lines = [f"🏦 <b>Курсы ЦБ РФ на {html.escape(result['date'])}</b>\n"]
    for r in result["rates"]:
        lines.append(
            f"• <b>{r['code']}</b> ({html.escape(r['name'])}): "
            f"{r['nominal']} {r['code']} = <b>{fmt_number(r['value'], 4)} ₽</b>"
        )
    if result.get("missing"):
        lines.append(f"\n⚠️ Нет данных для: {', '.join(result['missing'])}")
    return "\n".join(lines)


def format_crypto_prices(result: dict) -> str:
    lines = ["🪙 <b>Курсы криптовалют</b> <i>(CoinGecko)</i>\n"]
    for item in result["prices"]:
        prices = " · ".join(f"<b>{fmt_money(v, k)}</b>" for k, v in item["prices"].items())
        lines.append(f"• <b>{html.escape(item['coin'])}</b>: {prices}")
    if result.get("missing"):
        lines.append(f"\n⚠️ Не найдены: {', '.join(html.escape(m) for m in result['missing'])}")
    return "\n".join(lines)


def format_tool_result(tool: str, result: dict) -> str:
    rate = result.get("currency")

    if tool == "list_products":
        if not result["count"]:
            return "📭 В базе пока нет товаров."
        return (
            f"📦 <b>Все товары</b> — всего {result['count']}:\n"
            + fmt_products_grouped(result["products"], rate)
            + fmt_rate_note(rate)
        )

    if tool == "find_product":
        query = html.escape(result["query"])
        if not result["count"]:
            return f"🔍 По запросу «{query}» ничего не найдено. Попробуйте другое название."
        return (
            f"🔍 Найдено по запросу «{query}»: {result['count']}\n"
            + fmt_products_grouped(result["products"], rate)
            + fmt_rate_note(rate)
        )

    if tool == "find_products_by_category":
        category = html.escape(result["category"])
        if not result["count"]:
            return f"🏷 В категории «{category}» товаров не найдено."
        return (
            f"🏷 Товары категории «{category}»: {result['count']}\n"
            + fmt_products_grouped(result["products"], rate)
            + fmt_rate_note(rate)
        )

    if tool == "add_product":
        p = result["product"]
        price_line = f"💰 Цена: <b>{fmt_price(p['price'])}</b>"
        original = result.get("original_price")
        if original:
            cur = original["currency"]
            price_line += (
                f" (введено {fmt_money(original['amount'], cur['code'], cur['type'])}, "
                f"курс {html.escape(cur['source'])}: 1 {cur['code']} = {fmt_price(cur['rub_per_unit'])})"
            )
        return (
            "✅ <b>Товар добавлен!</b>\n\n"
            f"🆔 ID: <code>{p['id']}</code>\n"
            f"📝 Название: {html.escape(p['name'])}\n"
            f"🏷 Категория: {html.escape(p['category'])}\n"
            f"{price_line}"
        )

    if tool == "calculate":
        return (
            "🧮 <b>Результат вычисления</b>\n\n"
            f"<code>{html.escape(str(result['expression']))} = {html.escape(str(result['result']))}</code>"
        )

    if tool == "top_products":
        return format_top_products(result)

    if tool == "convert_currency":
        return format_conversion(result)

    if tool == "get_exchange_rates":
        return format_exchange_rates(result)

    if tool == "get_crypto_prices":
        return format_crypto_prices(result)

    return f"<pre>{html.escape(json.dumps(result, ensure_ascii=False, indent=2))}</pre>"


def summarize_for_history(tool: str, arguments: dict, result: dict) -> str:
    """Короткая сводка результата, чтобы LLM помнила контекст в следующих сообщениях."""
    if "products" in result:
        rate = result.get("currency")
        suffix = f" {rate['code']}" if rate else ""

        def describe(p: dict) -> str:
            converted = f", {p['price_converted']}{suffix}" if "price_converted" in p else ""
            return f"{p['name']} ({p['category']}, {p['price']} RUB{converted})"

        items = "; ".join(describe(p) for p in result["products"][:30])
        more = " ..." if result["count"] > 30 else ""
        summary = f"найдено {result['count']}: {items}{more}"
    else:
        summary = json.dumps(result, ensure_ascii=False)[:1500]
    return f"[Вызван инструмент {tool} с аргументами {json.dumps(arguments, ensure_ascii=False)}; результат: {summary}]"


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit and current:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


async def send_long(update: Update, text: str, parse_mode: str | None = ParseMode.HTML) -> None:
    for chunk in split_message(text):
        await update.effective_message.reply_text(chunk, parse_mode=parse_mode)


# ---------------------------------------------------------------------------
# Обработчики Telegram
# ---------------------------------------------------------------------------

HELP_TEXT = (
    "👋 <b>Привет! Я помощник по базе товаров.</b>\n\n"
    "Пишите обычным языком, например:\n"
    "• <i>покажи все товары</i>\n"
    "• <i>найди чай</i>\n"
    "• <i>покажи товары в категории электроника</i>\n"
    "• <i>добавь товар яблоки 120 фрукт</i>\n"
    "• <i>сколько будет (1290 + 3490) * 2</i>\n\n"
    "💱 Валюты и криптовалюты:\n"
    "• <i>покажи чай с ценами в долларах</i>\n"
    "• <i>сколько стоит ноутбук в биткоинах</i>\n"
    "• <i>курс доллара и евро</i>, <i>курс биткоина</i>\n"
    "• <i>переведи 5000 рублей в юани</i>\n\n"
    "📊 Аналитика:\n"
    "• <i>5 самых дорогих товаров</i>\n"
    "• <i>3 самых дешёвых товара в категориях книги и спорт</i>\n"
    "• <i>самый дорогой товар в каждой категории в евро</i>\n\n"
    "Команды:\n"
    "/help — подсказка\n"
    "/reset — очистить историю диалога"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.chat_data["history"] = []
    await update.effective_message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.chat_data["history"] = []
    await update.effective_message.reply_text("🧹 История диалога очищена.")


def remember(context: ContextTypes.DEFAULT_TYPE, user_text: str, assistant_text: str) -> None:
    history: list[dict] = context.chat_data.setdefault("history", [])
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": assistant_text})
    del history[: max(0, len(history) - config.HISTORY_LIMIT * 2)]


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = (update.effective_message.text or "").strip()
    if not user_text:
        return

    await update.effective_chat.send_action(ChatAction.TYPING)
    history = context.chat_data.setdefault("history", [])

    try:
        llm_reply = await ask_llm(history, user_text)
    except OpenAIError as exc:
        logger.exception("Ошибка LLM")
        await update.effective_message.reply_text(f"⚠️ Не удалось связаться с языковой моделью: {exc}")
        return

    tool_call = extract_tool_call(llm_reply)
    if tool_call is None:
        reply = llm_reply or "🤔 Не совсем понял запрос. Уточните, пожалуйста, что нужно сделать?"
        remember(context, user_text, reply)
        await send_long(update, reply, parse_mode=None)
        return

    tool, arguments = tool_call["tool"], tool_call["arguments"]
    logger.info("Чат %s → %s %s", update.effective_chat.id, tool, arguments)

    try:
        result = await mcp.call_tool(tool, arguments)
    except MCPError as exc:
        remember(context, user_text, f"[Ошибка инструмента {tool}: {exc}]")
        await update.effective_message.reply_text(f"⚠️ Ошибка при выполнении операции: {exc}")
        return

    remember(context, user_text, summarize_for_history(tool, arguments, result))
    await send_long(update, format_tool_result(tool, result))


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Необработанная ошибка", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("⚠️ Произошла внутренняя ошибка. Попробуйте ещё раз.")


async def post_init(application: Application) -> None:
    try:
        info = await mcp.initialize()
        tools = await mcp.list_tools()
        logger.info(
            "Подключено к MCP-серверу %s, инструменты: %s",
            info.get("serverInfo", {}).get("name"),
            ", ".join(t["name"] for t in tools),
        )
    except MCPError as exc:
        logger.warning("MCP-сервер пока недоступен: %s. Бот попробует подключиться позже.", exc)


async def post_shutdown(application: Application) -> None:
    await mcp.close()


def main() -> None:
    application = (
        ApplicationBuilder()
        .token(config.TELEGRAM_API_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(on_error)

    logger.info("Бот запущен. Модель: %s, MCP: %s", config.LLM_MODEL, config.MCP_SERVER_URL)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
