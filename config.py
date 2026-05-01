"""
Конфігурація застосунку.
Використовує dataclass для типізованого зберігання налаштувань.
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class AppConfig:
    """Незмінна конфігурація застосунку (Immutable Value Object)."""

    bot_token: str
    anthropic_api_key: str
    db_path: str
    claude_model: str
    debug: bool
    web_host: str
    web_port: int

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Фабричний метод — створює конфіг зі змінних середовища."""
        bot_token = os.getenv("BOT_TOKEN", "")
        api_key   = os.getenv("ANTHROPIC_API_KEY", "")

        if not bot_token:
            raise ValueError("BOT_TOKEN не вказано в .env!")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY не вказано в .env!")

        return cls(
            bot_token=bot_token,
            anthropic_api_key=api_key,
            db_path=os.getenv("DB_PATH", "receipts.db"),
            claude_model=os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
            debug=os.getenv("DEBUG", "False").lower() == "true",
            web_host=os.getenv("WEB_HOST", "127.0.0.1"),
            web_port=int(os.getenv("WEB_PORT", "5000")),
        )


# Єдиний екземпляр конфігурації для всього проєкту
config = AppConfig.from_env()

# Зворотна сумісність зі старими імпортами
BOT_TOKEN         = config.bot_token
ANTHROPIC_API_KEY = config.anthropic_api_key
DB_PATH           = config.db_path
CLAUDE_MODEL      = config.claude_model
DEBUG             = config.debug
