# product-mcp + Telegram-бот

Проект из двух компонентов:

1. **`mcp_server/`** — MCP-сервер `product-mcp` (FastAPI + SQLite3). Хранит товары в `products.db`,
   при первом запуске заполняет базу 100 тестовыми товарами и предоставляет MCP-инструменты.
2. **`telegram_bot/`** — Telegram-бот (python-telegram-bot). Отправляет сообщения пользователя в LLM
   `openai/gpt-5-mini` через [ProxyAPI](https://proxyapi.ru), LLM решает, нужен ли инструмент,
   бот вызывает его на MCP-сервере по HTTP и отправляет красиво оформленный ответ.

```
.
├── .env                  # ключи и настройки (заполнить!)
├── .env.example          # шаблон .env
├── mcp_server/
│   ├── server.py         # HTTP-сервер, MCP (JSON-RPC 2.0) на POST /mcp
│   ├── db.py             # SQLite3: таблица products, автозаполнение
│   ├── tools.py          # инструменты и их MCP JSON Schema, безопасный калькулятор на AST
│   ├── products.db       # создаётся автоматически при запуске
│   └── requirements.txt
└── telegram_bot/
    ├── bot.py            # логика бота
    ├── config.py         # чтение переменных из .env
    ├── mcp_client.py     # клиент MCP-инструментов
    ├── system_prompt.txt # системный промпт для LLM
    └── requirements.txt
```

## MCP-инструменты

| Инструмент | Аргументы | Описание |
| --- | --- | --- |
| `list_products` | `currency?` | все товары |
| `find_product` | `name: string, currency?` | поиск по части названия |
| `find_products_by_category` | `category: string, currency?` | поиск по категории |
| `add_product` | `name: string, category: string, price: number, currency?` | добавить товар (цена в валюте пересчитывается в рубли) |
| `calculate` | `expression: string` | безопасный калькулятор (AST, без `eval`) |
| `top_products` | `order: "expensive"\|"cheap", limit?, categories?, per_category?, currency?` | самые дорогие/дешёвые товары + мин/макс/средняя цена |
| `convert_currency` | `amount: number, to_currency: string, from_currency?` | конвертация между рублём, валютами и криптовалютами |
| `get_exchange_rates` | `currencies?, date?` | курсы ЦБ РФ (`cbr.ru/scripts/XML_daily.asp`) |
| `get_crypto_prices` | `coins?, currencies?` | цены криптовалют (CoinGecko `simple/price`, без ключа) |

Цены в базе хранятся в рублях. Параметр `currency` — код ЦБ РФ (`USD`, `EUR`, `CNY`…) или тикер
криптовалюты (`BTC`, `ETH`, `USDT`, `TON`…). Курсы ЦБ кэшируются на 1 час, цены CoinGecko — на 1 минуту.

## Запуск (Windows, PowerShell)

### 1. Установить зависимости

```powershell
cd "путь\к\проекту"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r mcp_server\requirements.txt -r telegram_bot\requirements.txt
```

> На Linux/macOS: `python3 -m venv .venv && source .venv/bin/activate`.

### 2. Заполнить `.env`

Откройте `.env` в корне проекта и укажите:

- `TELEGRAM_API_TOKEN` — токен бота от [@BotFather](https://t.me/BotFather);
- `ProxyAPI_KEY` — ключ ProxyAPI из [личного кабинета](https://console.proxyapi.ru/keys).

Остальные значения можно оставить по умолчанию.

### 3. Запустить MCP-сервер (терминал №1)

```powershell
cd mcp_server
python server.py
```

Сервер стартует на `http://127.0.0.1:8000`, при первом запуске создаст `products.db` со 100 товарами.
Проверка: откройте `http://127.0.0.1:8000/tools` — увидите список инструментов в формате MCP JSON Schema
(или `http://127.0.0.1:8000/docs` — Swagger UI).

### 4. Запустить бота (терминал №2, с активированным `.venv`)

```powershell
cd telegram_bot
python bot.py
```

Напишите боту в Telegram: «покажи все товары», «найди чай», «покажи товары в категории электроника»,
«добавь товар яблоки 120 фрукт», «сколько будет 2+2».

## Протокол MCP

`POST /mcp` принимает сообщения JSON-RPC 2.0: `initialize`, `notifications/initialized`, `ping`,
`tools/list`, `tools/call`. Пример:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/mcp -ContentType "application/json" `
  -Body '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"calculate","arguments":{"expression":"(2+3)*4"}}}'
```

Сервер можно подключить и к MCP-клиентам с поддержкой Streamable HTTP (например, Cursor):

```json
{ "mcpServers": { "product-mcp": { "url": "http://127.0.0.1:8000/mcp" } } }
```

Также есть упрощённые REST-эндпоинты: `GET /health`, `GET /tools`, `POST /tools/{tool_name}` (тело — JSON с аргументами).

## Примечания по зависимостям

- `sqlite3` входит в стандартную библиотеку Python, отдельно не устанавливается.
- У ProxyAPI нет собственного pip-пакета: API совместим с OpenAI, поэтому используется пакет `openai`
  с `base_url=https://api.proxyapi.ru/v1`.
- Пакет `mcp-spec` не нужен: протокол реализован непосредственно в `server.py`.
