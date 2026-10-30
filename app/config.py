import os


def _env(name: str, default: str = "") -> str:
    """Le a variavel; se estiver ausente OU vazia, usa o padrao."""
    return os.getenv(name) or default


_db = _env("DATABASE_URL", "sqlite:///local.db")
if _db.startswith("postgres://"):
    _db = _db.replace("postgres://", "postgresql+psycopg2://", 1)
elif _db.startswith("postgresql://"):
    _db = _db.replace("postgresql://", "postgresql+psycopg2://", 1)
DATABASE_URL = _db

ML_CLIENT_ID = _env("ML_CLIENT_ID")
ML_CLIENT_SECRET = _env("ML_CLIENT_SECRET")
ML_SITE = _env("ML_SITE", "MLB")

# Termos de reserva: so sao usados se nao houver nenhum cadastrado no /admin
SEARCH_TERMS = [t.strip() for t in _env("SEARCH_TERMS", "fone bluetooth,air fryer").split(",") if t.strip()]
MIN_DISCOUNT_PCT = float(_env("MIN_DISCOUNT_PCT", "15"))
RESULTS_PER_TERM = int(_env("RESULTS_PER_TERM", "20"))

AFFILIATE_URL_TEMPLATE = _env("AFFILIATE_URL_TEMPLATE", "{url}")

TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID")
MAX_TELEGRAM_PER_RUN = int(_env("MAX_TELEGRAM_PER_RUN", "5"))

ADMIN_TOKEN = _env("ADMIN_TOKEN")
