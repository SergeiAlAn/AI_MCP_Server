"""Настройки бота. Все секреты читаются из файла .env."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# .env ищется в папке бота, затем в корне проекта (первый найденный ключ имеет приоритет).
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR.parent / ".env")


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Переменная окружения {name} не задана. "
            f"Скопируйте .env.example в .env и заполните значения."
        )
    return value


TELEGRAM_API_TOKEN: str = _required("TELEGRAM_API_TOKEN")
ProxyAPI_KEY: str = _required("ProxyAPI_KEY")

ProxyAPI_BASE_URL: str = os.getenv("ProxyAPI_BASE_URL", "https://api.proxyapi.ru/v1").strip()
LLM_MODEL: str = os.getenv("LLM_MODEL", "openai/gpt-5-mini").strip()

MCP_SERVER_URL: str = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8000/mcp").strip()

SYSTEM_PROMPT_PATH: Path = BASE_DIR / "system_prompt.txt"
HISTORY_LIMIT: int = int(os.getenv("HISTORY_LIMIT", "10"))
