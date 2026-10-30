import os

_db = os.getenv("DATABASE_URL", "sqlite:///local.db")
if _db.startswith("postgres://"):
    _db = _db.replace("postgres://", "postgresql+psycopg2://", 1)
elif _db.startswith("postgresql://"):
    _db = _db.replace("postgresql://", "postgresql+psycopg2://", 1)
DATABASE_URL = _db

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "")
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "")
ML_SITE = os.getenv("ML_SITE", "MLB")

SEARCH_TERMS = [t.strip() for t in os.getenv("SEARCH_TERMS", "fone bluetooth,air fryer").split(",") if t.strip()]
MIN_DISCOUNT_PCT = float(os.getenv("MIN_DISCOUNT_PCT", "15"))
RESULTS_PER_TERM = int(os.getenv("RESULTS_PER_TERM", "20"))

AFFILIATE_URL_TEMPLATE = os.getenv("AFFILIATE_URL_TEMPLATE", "{url}")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
MAX_TELEGRAM_PER_RUN = int(os.getenv("MAX_TELEGRAM_PER_RUN", "5"))

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
