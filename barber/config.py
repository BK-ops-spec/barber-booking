import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(os.environ.get("BARBER_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("BARBER_DB_PATH", DATA_DIR / "barber.db"))

HOST = os.environ.get("BARBER_HOST", "127.0.0.1")
PORT = int(os.environ.get("BARBER_PORT", "8000"))

SESSION_COOKIE = "barber_session"
SESSION_TTL_SECONDS = 60 * 60 * 12
SESSION_COOKIE_SECURE = os.environ.get("BARBER_SECURE_COOKIES", "").lower() in {"1", "true", "yes"}

DEFAULT_ADMIN_USERNAME = os.environ.get("BARBER_ADMIN_USER", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("BARBER_ADMIN_PASS", "admin123")

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6
