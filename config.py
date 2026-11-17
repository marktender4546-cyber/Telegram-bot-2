"""Environment-based configuration for local and Render deployments."""
import os
from pathlib import Path

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "0").strip()
try:
    ADMIN_ID = int(ADMIN_ID_RAW or "0")
except ValueError as exc:
    raise RuntimeError("ADMIN_ID raqamli Telegram ID bo'lishi kerak.") from exc

DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "Asia/Tashkent")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "5"))
DATABASE_FILE = os.getenv("DATABASE_PATH", str(Path(__file__).resolve().parent / "remind_me.db"))
MAX_REMINDERS_PER_USER = int(os.getenv("MAX_REMINDERS_PER_USER", "500"))
