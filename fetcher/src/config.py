"""
Runtime configuration — all values sourced from environment / .env file.

Import this module and reference module-level constants.
e.g.  import config  →  config.REDIS_DB_HOST, config.DEBUG, etc.
"""

import os
from dotenv import load_dotenv

# Load .env from the project root (or current working dir).
# python-dotenv searches CWD by default; this is explicit.
load_dotenv()

# ── Redis ─────────────────────────────────────────────────────
REDIS_DB_HOST: str = os.getenv("REDIS_HOST", "localhost")
REDIS_DB_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
REDIS_STREAM: str = os.getenv("REDIS_STREAM", "fetcher")
REDIS_STREAM_GROUP: str = os.getenv("REDIS_STREAM_GROUP", "fetcher-workers")
REDIS_STREAM_FAILED: str = os.getenv("REDIS_STREAM_FAILED", "fetcher:failed")
REDIS_SEEN_SET: str = os.getenv("REDIS_SEEN_SET", "fetcher:seen_urls")
REDIS_POOL_MIN: int = int(os.getenv("REDIS_POOL_MIN", "2"))
REDIS_POOL_MAX: int = int(os.getenv("REDIS_POOL_MAX", "10"))

# ── PostgreSQL ────────────────────────────────────────────────
POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB: str = os.getenv("POSTGRES_DB", "fetcher")
POSTGRES_USER: str = os.getenv("POSTGRES_USER", "fetcher")
POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "fetcher")
POSTGRES_POOL_MIN: int = int(os.getenv("POSTGRES_POOL_MIN", "2"))
POSTGRES_POOL_MAX: int = int(os.getenv("POSTGRES_POOL_MAX", "10"))

# ── Workers ────────────────────────────────────────────────────
NUMBER_OF_WORKERS: int = int(os.getenv("NUMBER_OF_WORKERS", "5"))

# ── Crawl defaults ────────────────────────────────────────────
DEFAULT_MAX_DEPTH: int = int(os.getenv("DEFAULT_MAX_DEPTH", "50"))
DEFAULT_RETRY_COUNT: int = int(os.getenv("DEFAULT_RETRY_COUNT", "3"))
DEFAULT_RETRY_DELAY: int = int(os.getenv("DEFAULT_RETRY_DELAY", "5"))
REQUEST_DELAY_MIN: float = float(os.getenv("REQUEST_DELAY_MIN", "2"))
REQUEST_DELAY_MAX: float = float(os.getenv("REQUEST_DELAY_MAX", "5"))

# ── Logging ────────────────────────────────────────────────────
# Explicit LOG_LEVEL takes precedence; when unset, DEBUG=true → DEBUG else INFO.
_raw_log_level = os.getenv("LOG_LEVEL", "").strip().upper()
if _raw_log_level:
    LOG_LEVEL: str = _raw_log_level
else:
    LOG_LEVEL: str = "DEBUG" if os.getenv("DEBUG", "false").strip().lower() == "true" else "INFO"
LOG_DIR: str = os.getenv("LOG_DIR", "logs")
LOG_FLUSH_INTERVAL: int = int(os.getenv("LOG_FLUSH_INTERVAL", "60"))

# ── App ────────────────────────────────────────────────────────
DEBUG: bool = os.getenv("DEBUG", "false").strip().lower() == "true"
