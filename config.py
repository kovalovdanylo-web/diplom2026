import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
DB_PATH = os.getenv("DB_PATH", "receipts.db")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
DEBUG = os.getenv("DEBUG", "False").lower() == "true"

# Валідація обов'язкових параметрів
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не вказано в .env!")
if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY не вказано в .env!")
