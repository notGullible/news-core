"""
Runtime configuration — all values sourced from environment / .env file.

Uses ``python-dotenv``'s ``find_dotenv()`` to locate the ``.env`` file
by walking up from the current working directory.  This works regardless
of which package directory the process is started from.

Import this module and reference module-level constants::

    from common import config
    config.REDIS_DB_HOST
    config.DEBUG
"""

import os
from dotenv import load_dotenv, find_dotenv
from qdrant_client.http import models as qmodels

# find_dotenv walks up from CWD — .env can live at the repo root.
load_dotenv(find_dotenv())

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

# ── Qdrant (Vector DB) ──────────────────────────────────────
QDRANT_HOST: str = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT: int = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_API_KEY: str | None = os.getenv("QDRANT_API_KEY") or None
QDRANT_USE_TLS: bool = os.getenv("QDRANT_USE_TLS", "false").strip().lower() == "true"
QDRANT_DEFAULT_COLLECTION: str = os.getenv("QDRANT_DEFAULT_COLLECTION", "articles")

# ── Embeddings ──────────────────────────────────────────────
EMBEDDING_MODEL_NAME: str = os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
EMBEDDING_DEFAULT_DISTANCE = qmodels.Distance.COSINE 

# ── Viewer ─────────────────────────────────────────────────────
PAGE_SIZE: int = int(os.getenv("PAGE_SIZE", "25"))

# ── App ────────────────────────────────────────────────────────
DEBUG: bool = os.getenv("DEBUG", "false").strip().lower() == "true"
