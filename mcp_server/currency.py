"""Курсы валют ЦБ РФ и цены криптовалют CoinGecko с кэшированием.

Все цены товаров в базе хранятся в рублях; для конвертации используется курс
«сколько рублей стоит 1 единица валюты».
"""

import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime

import httpx

CBR_DAILY_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"

CBR_CACHE_TTL = 60 * 60
CRYPTO_CACHE_TTL = 60
HTTP_TIMEOUT = 15.0
HTTP_HEADERS = {"User-Agent": "product-mcp/1.0", "Accept": "application/json, application/xml"}

POPULAR_FIAT = ["USD", "EUR", "CNY", "GBP", "JPY", "CHF", "KZT", "BYN", "TRY", "AED"]

CRYPTO_IDS: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "USDT": "tether",
    "USDC": "usd-coin",
    "BNB": "binancecoin",
    "SOL": "solana",
    "XRP": "ripple",
    "TON": "the-open-network",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "TRX": "tron",
    "LTC": "litecoin",
    "DOT": "polkadot",
    "XMR": "monero",
}

# Основы слов, по которым распознаются названия валют на русском (и некоторые символы).
CURRENCY_ALIASES: list[tuple[str, str]] = [
    ("белорусск", "BYN"),
    ("₽", "RUB"), ("руб", "RUB"),
    ("$", "USD"), ("доллар", "USD"), ("бакс", "USD"),
    ("€", "EUR"), ("евро", "EUR"),
    ("¥", "CNY"), ("юан", "CNY"),
    ("£", "GBP"), ("фунт", "GBP"),
    ("иен", "JPY"), ("йен", "JPY"),
    ("франк", "CHF"),
    ("тенге", "KZT"),
    ("лир", "TRY"),
    ("дирхам", "AED"),
    ("гривн", "UAH"),
    ("рупи", "INR"),
    ("биткоин", "BTC"), ("биткойн", "BTC"), ("битко", "BTC"), ("bitcoin", "BTC"),
    ("эфир", "ETH"), ("ethereum", "ETH"),
    ("тезер", "USDT"), ("tether", "USDT"),
    ("солан", "SOL"), ("solana", "SOL"),
    ("тонкоин", "TON"), ("toncoin", "TON"),
    ("догикоин", "DOGE"), ("доге", "DOGE"), ("dogecoin", "DOGE"),
    ("лайткоин", "LTC"), ("litecoin", "LTC"),
    ("рипл", "XRP"), ("ripple", "XRP"),
    ("кардано", "ADA"), ("cardano", "ADA"),
]


class CurrencyError(Exception):
    """Ошибка получения курса или неизвестная валюта."""


_lock = threading.Lock()
_cbr_cache: dict[str, tuple[float, dict]] = {}
_crypto_cache: dict[tuple, tuple[float, dict]] = {}


# ---------------------------------------------------------------------------
# ЦБ РФ
# ---------------------------------------------------------------------------

def _normalize_date(value: str | None) -> str | None:
    """Приводит дату к формату ЦБ dd/mm/yyyy. Принимает YYYY-MM-DD, DD.MM.YYYY, DD/MM/YYYY."""
    if not value or not str(value).strip():
        return None
    value = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(value, fmt).date()
            break
        except ValueError:
            continue
    else:
        raise CurrencyError(f"Не удалось распознать дату «{value}». Используйте формат ДД.ММ.ГГГГ")
    if parsed > date.today():
        raise CurrencyError("Курс ЦБ на будущую дату недоступен")
    return parsed.strftime("%d/%m/%Y")


def _to_float(text: str | None) -> float:
    return float((text or "0").replace(",", ".").replace(" ", ""))


def get_cbr_rates(on_date: str | None = None) -> dict:
    """Курсы ЦБ РФ: {"date": "10.10.2026", "rates": {"USD": {...}, ...}}."""
    date_req = _normalize_date(on_date)
    cache_key = date_req or "today"
    with _lock:
        cached = _cbr_cache.get(cache_key)
        if cached and time.time() - cached[0] < CBR_CACHE_TTL:
            return cached[1]

    params = {"date_req": date_req} if date_req else None
    try:
        response = httpx.get(CBR_DAILY_URL, params=params, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except (httpx.HTTPError, ET.ParseError) as exc:
        raise CurrencyError(f"Не удалось получить курсы ЦБ РФ: {exc}") from exc

    rates: dict[str, dict] = {}
    for valute in root.findall("Valute"):
        code = (valute.findtext("CharCode") or "").strip().upper()
        nominal = int(_to_float(valute.findtext("Nominal")) or 1)
        value = _to_float(valute.findtext("Value"))
        if not code or not value:
            continue
        rates[code] = {
            "code": code,
            "name": (valute.findtext("Name") or "").strip(),
            "nominal": nominal,
            "value": value,
            "rate": value / nominal,
        }
    if not rates:
        raise CurrencyError("ЦБ РФ не вернул курсы на указанную дату")

    data = {"date": root.get("Date", ""), "source": "ЦБ РФ", "rates": rates}
    with _lock:
        _cbr_cache[cache_key] = (time.time(), data)
    return data


# ---------------------------------------------------------------------------
# CoinGecko
# ---------------------------------------------------------------------------

def get_crypto_prices(coin_ids: list[str], vs_currencies: list[str]) -> dict:
    """Цены монет CoinGecko: {"bitcoin": {"rub": 7048882, "usd": 82773}, ...}."""
    ids = sorted({c.strip().lower() for c in coin_ids if c and c.strip()})
    vs = sorted({v.strip().lower() for v in vs_currencies if v and v.strip()})
    if not ids or not vs:
        raise CurrencyError("Не указаны монеты или валюты для CoinGecko")
    cache_key = (tuple(ids), tuple(vs))
    with _lock:
        cached = _crypto_cache.get(cache_key)
        if cached and time.time() - cached[0] < CRYPTO_CACHE_TTL:
            return cached[1]

    try:
        response = httpx.get(
            COINGECKO_PRICE_URL,
            params={"ids": ",".join(ids), "vs_currencies": ",".join(vs)},
            headers=HTTP_HEADERS,
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code == 429:
            raise CurrencyError("CoinGecko временно ограничил запросы, попробуйте через минуту")
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CurrencyError(f"Не удалось получить цены CoinGecko: {exc}") from exc

    with _lock:
        _crypto_cache[cache_key] = (time.time(), data)
    return data


# ---------------------------------------------------------------------------
# Распознавание валют и конвертация
# ---------------------------------------------------------------------------

def normalize_currency(value: str) -> str:
    """Возвращает код валюты (USD, EUR, BTC...) или id монеты CoinGecko в нижнем регистре."""
    raw = str(value or "").strip()
    if not raw:
        raise CurrencyError("Не указана валюта")
    upper = raw.upper()
    if upper == "RUR":
        return "RUB"
    if re.fullmatch(r"[A-Z]{3,5}", upper) and (upper == "RUB" or upper in CRYPTO_IDS or len(upper) == 3):
        return upper
    lowered = raw.lower()
    for stem, code in CURRENCY_ALIASES:
        if stem in lowered:
            return code
    if re.fullmatch(r"[a-z0-9-]+", lowered):
        return lowered  # возможно, id монеты CoinGecko (например, "shiba-inu")
    raise CurrencyError(f"Неизвестная валюта: «{raw}»")


def get_rate(currency: str) -> dict:
    """Сколько рублей стоит 1 единица валюты: {"code", "type", "rub_per_unit", "source", "date"}."""
    code = normalize_currency(currency)
    if code == "RUB":
        return {"code": "RUB", "type": "fiat", "name": "Российский рубль", "rub_per_unit": 1.0,
                "source": "—", "date": ""}

    is_crypto_code = code in CRYPTO_IDS or code.islower()
    if not is_crypto_code:
        cbr = get_cbr_rates()
        info = cbr["rates"].get(code)
        if info is None:
            raise CurrencyError(f"Валюта {code} не найдена в курсах ЦБ РФ")
        return {"code": code, "type": "fiat", "name": info["name"], "rub_per_unit": info["rate"],
                "source": cbr["source"], "date": cbr["date"]}

    coin_id = CRYPTO_IDS.get(code, code)
    prices = get_crypto_prices([coin_id], ["rub"])
    rub_price = prices.get(coin_id, {}).get("rub")
    if not rub_price:
        raise CurrencyError(f"Криптовалюта «{code}» не найдена на CoinGecko")
    display_code = code if code in CRYPTO_IDS else coin_id
    return {"code": display_code.upper(), "type": "crypto", "name": coin_id, "rub_per_unit": float(rub_price),
            "source": "CoinGecko", "date": datetime.now().strftime("%d.%m.%Y %H:%M")}


def round_amount(value: float, currency_type: str) -> float:
    if currency_type == "crypto":
        return float(f"{value:.6g}")
    return round(value, 2)


def convert(amount: float, from_currency: str, to_currency: str) -> dict:
    source = get_rate(from_currency)
    target = get_rate(to_currency)
    rub_amount = amount * source["rub_per_unit"]
    result = rub_amount / target["rub_per_unit"]
    return {
        "amount": amount,
        "from": source,
        "to": target,
        "rub_amount": round(rub_amount, 2),
        "result": round_amount(result, target["type"]),
    }
