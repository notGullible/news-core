# NG Core — Fetcher/Scraper/Crawler Architecture Plan

## Overview

Transform the existing single-purpose Reuters scraper into an **extensible, multi-site web crawling pipeline** that:

1. Accepts seed URLs via `loader.py` → Redis stream
2. Crawls all pages under the seed's URL prefix
3. Auto-detects whether a page is an article (extract + store) or a listing page (extract links + continue)
4. Stores structured article data (headline, content, URL, hash, timestamp) in PostgreSQL
5. Supports pluggable per-site modules via a `@register_module` decorator
6. Auto-decides whether a site needs HTTP requests or a headless browser (Chrome via botasaurus)

---

## Architecture Diagram

```
┌─────────────┐     ┌──────────────┐     ┌────────────────────────────┐
│  loader.py  │────▶│ Redis Stream │────▶│  5 Worker Processes (spawn) │
│  (seeds)    │     │  "fetcher"   │     │                            │
└─────────────┘     └──────────────┘     │  Each worker:              │
                                         │  1. Connect Redis + PG     │
┌─────────────┐                          │  2. Dequeue stream msg     │
│  sites.py   │                          │  3. ModuleManager.route()  │
│  (URL list) │                          │  4. HTTP-first → extract   │
└─────────────┘                          │  5. If JS: browser→extract │
                                         │  6. If article: PG upsert  │
                                         │  7. Extract links          │
                                         │  8. Dedup + re-enqueue     │
                                         │  9. Loop forever           │
                                         └────────────────────────────┘
                                                    │
                    ┌───────────────────────────────┼──────────────────┐
                    │                               │                  │
                    ▼                               ▼                  ▼
            ┌──────────────┐              ┌──────────────┐   ┌──────────────┐
            │  PostgreSQL  │              │ Redis Set    │   │ Redis Stream │
            │  articles    │              │ seen_urls    │   │ fetcher:     │
            │  crawl_hist  │              │ (dedup)      │   │ failed (DLQ) │
            └──────────────┘              └──────────────┘   └──────────────┘
```

---

## Phase 1 — Infrastructure

### 1.1: Add PostgreSQL to `compose.yaml`

Add a `postgres:16-alpine` service alongside Redis:

- Container: `postgres:16-alpine`
- Port: `5432:5432`
- Environment: `POSTGRES_DB=fetcher`, `POSTGRES_USER=fetcher`, `POSTGRES_PASSWORD=fetcher`
- Volume: `pgdata:/var/lib/postgresql/data`
- Healthcheck: `pg_isready -U fetcher -d fetcher`
- Volume declaration: `pgdata:` alongside existing `redis-ngdata:`

### 1.2: Add Python dependencies to `pyproject.toml`

New dependencies:
- `sqlalchemy[asyncio]>=2.0` — async ORM with asyncpg driver
- `asyncpg>=0.30` — PostgreSQL async driver
- `python-dotenv>=1.0` — .env file loading
- `alembic>=1.14` — migrations (for non-DEBUG mode)

Run `uv sync` after updating.

### 1.3: Create `.env` and update `config.py`

**`.env` file** (gitignored):
```
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_STREAM=fetcher
REDIS_STREAM_FAILED=fetcher:failed
REDIS_SEEN_SET=fetcher:seen_urls
REDIS_POOL_MIN=2
REDIS_POOL_MAX=10

POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=fetcher
POSTGRES_USER=fetcher
POSTGRES_PASSWORD=fetcher
POSTGRES_POOL_MIN=2
POSTGRES_POOL_MAX=10

NUMBER_OF_WORKERS=5

DEFAULT_MAX_DEPTH=3
DEFAULT_RETRY_COUNT=3
DEFAULT_RETRY_DELAY=5
REQUEST_DELAY_MIN=2
REQUEST_DELAY_MAX=5

DEBUG=false
```

**`config.py`** reads all values from `os.environ` (loaded via `dotenv`), provides typed module-level constants. Existing constants migrate to env-based loading. Backward-compatible: existing code that imports `config.REDIS_DB_HOST` etc. should still work — keep aliases for old names or rename internal refs.

### 1.4: Create `mypostgres.py` — PostgreSQL Connection Manager

Pattern: mirror `myredis.py`. Pool-based async connection manager.

```python
class MyPostgres:
    _engine = create_async_engine(...)
    
    async def init_db(self) -> bool:
        # Verify connection
        # In DEBUG mode: create_all tables
        # In non-DEBUG mode: run Alembic migrations
    
    async def close_db(self):
        # Dispose engine
```

- Connection string assembled from config
- `pool_size` and `max_overflow` from config
- `get_session()` returns `AsyncSession` context manager

### 1.5: Create `models.py` — SQLAlchemy ORM Models

```python
class Article(Base):
    __tablename__ = "articles"
    
    id            = Column(BigInteger, primary_key=True, autoincrement=True)
    url           = Column(Text, nullable=False, unique=True, index=True)
    source_domain = Column(Text, nullable=False, index=True)
    headline      = Column(Text, nullable=True)
    content       = Column(Text, nullable=True)
    content_hash  = Column(Text, nullable=False)  # SHA-256(headline + content)
    scraped_at    = Column(TIMESTAMPTZ, nullable=False, server_default=func.now())
    first_seen_at = Column(TIMESTAMPTZ, nullable=False, server_default=func.now())
    updated_at    = Column(TIMESTAMPTZ, nullable=False, server_default=func.now(), onupdate=func.now())

class CrawlHistory(Base):
    __tablename__ = "crawl_history"
    
    id            = Column(BigInteger, primary_key=True, autoincrement=True)
    article_id    = Column(BigInteger, ForeignKey("articles.id"), nullable=True)
    url           = Column(Text, nullable=False, index=True)
    content_hash  = Column(Text, nullable=False)
    scraped_at    = Column(TIMESTAMPTZ, nullable=False, server_default=func.now())
```

**Upsert logic:**
- On scrape: `INSERT ... ON CONFLICT (url) DO UPDATE SET headline=EXCLUDED.headline, content=EXCLUDED.content, content_hash=EXCLUDED.content_hash, updated_at=NOW()`
- Always insert into `crawl_history` (success or failure)

### 1.6: Table Creation vs. Migration Logic

In `mypostgres.py.init_db()`:
```python
if config.DEBUG:
    await conn.run_sync(Base.metadata.create_all)
else:
    # Run alembic upgrade head programmatically
    import alembic.config
    alembic.config.main(argv=["upgrade", "head"])
```

`Base` is imported from `models.py`.

### 1.7: Database teardown

`close_db()` in `mypostgres.py` disposes the engine. Called per-worker on shutdown (like `close_redispool()`).

---

## Phase 2 — Module System

### 2.1: Refactor `modules/services.py` — Transport Layer

Renamed conceptually. `Fetcher` and `Scrapper` remain but become **transport-layer wrappers** invoked by `ModuleManager`, not subclassed by site modules.

- `Fetcher`: wraps `@request` from botasaurus — lightweight HTTP
- `Scrapper`: wraps `@browser` — headless Chrome browser

These are called by `ModuleManager` as:
```python
async def fetch_via_http(url) -> (soup, driver_or_none)
async def fetch_via_browser(url) -> (soup, driver)
```

### 2.2: Create `modules/base.py` — BaseModule with Shared Extractors

```python
class ArticleData:
    url: str
    source_domain: str
    headline: str | None
    content: str | None
    content_hash: str
    
    @classmethod
    def from_extraction(cls, url, domain, headline, content):
        # Computes SHA-256 hash of headline+content
        ...


class BaseModule:
    domain: str = ""  # e.g., "reuters.com" — override in subclass
    
    def extract(self, soup, driver, url: str) -> ArticleData | None:
        """Extract article data. Returns None if page is not an article."""
        headline = self._extract_headline(soup)
        content = self._extract_content(soup)
        if not headline and not content:
            return None  # Not an article → listing page
        return ArticleData.from_extraction(
            url=url,
            domain=self.domain,
            headline=headline,
            content=content,
        )
    
    def extract_links(self, soup, url: str, seed_prefix: str) -> list[str]:
        """Extract in-scope links. Base impl: all <a href> filtered by prefix."""
        links = []
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"])
            if href.startswith(seed_prefix):
                links.append(href)
        return list(set(links))  # dedup within page
    
    # ── Shared extraction helpers ──
    def _extract_headline(self, soup) -> str | None:
        """Try: og:title → h1 → title tag"""
        for selector in (
            soup.find("meta", property="og:title"),
            soup.find("h1"),
            soup.find("title"),
        ):
            if selector:
                text = selector.get("content") if selector.name == "meta" else selector.get_text()
                if text:
                    return text.strip()
        return None
    
    def _extract_content(self, soup) -> str | None:
        """Try: <article> → .article-body → .article__body → <main> → <body>"""
        container = (
            soup.find("article")
            or soup.find(class_="article-body")
            or soup.find(class_="article__body")
            or soup.find(class_="article-content")
            or soup.find("main")
            or soup.find("body")
        )
        if not container:
            return None
        paragraphs = [p.get_text(strip=True) for p in container.find_all("p") if p.get_text(strip=True)]
        return "\n\n".join(paragraphs) if paragraphs else None
    
    def _extract_date(self, soup) -> str | None:
        """Try: article:published_time meta → pubdate → time element"""
        ...
    
    def _extract_author(self, soup) -> str | None:
        """Try: author meta → byline classes"""
        ...
    
    def _extract_section(self, soup) -> str | None:
        """Try: article:section meta → breadcrumbs"""
        ...
```

Site modules override `_extract_headline`, `_extract_content`, `extract_links`, etc. if needed. For Reuters, most defaults will work — only `_extract_section` and maybe `_extract_headline` need overrides (to strip " | Reuters" suffix).

### 2.3: Create `modules/registry.py` — Module Registration

```python
_registry: dict[str, type[BaseModule]] = {}  # domain → module class

def register_module(domain: str):
    """Decorator to register a site module."""
    def decorator(cls):
        _registry[domain] = cls
        return cls
    return decorator

def get_module(domain: str) -> type[BaseModule] | None:
    """Look up a module by domain."""
    return _registry.get(domain)

def get_all_modules() -> dict:
    """Return the full registry."""
    return _registry.copy()
```

### 2.4: Update `modules/__init__.py` — Explicit Imports

```python
# Import all module files so decorators execute
from .Reuters.module import ReutersModule
# from .BBC.module import BBCModule   # future
# from .CNN.module import CNNModule    # future

__all__ = ["ReutersModule"]
```

Adding a new site = one new directory + one line here + one `module.py` file.

### 2.5: Merge Reuters scraper → `modules/Reuters/module.py`

Combine `fetch.py` and `scrap.py` into one `module.py`:

```python
from ..base import BaseModule
from ..registry import register_module

@register_module(domain="reuters.com")
class ReutersModule(BaseModule):
    # Only override what's Reuters-specific
    def _extract_headline(self, soup):
        text = super()._extract_headline(soup)
        if text:
            for suffix in (" | Reuters", " - Reuters"):
                if text.endswith(suffix):
                    text = text[:-len(suffix)]
        return text
    
    def _extract_section(self, soup):
        # Reuters-specific section extraction
        ...
```

The old `fetch.py` and `scrap.py` can be deleted.

### 2.6: Refactor `module_manager.py` — New Routing Logic

```python
class ModuleManager:
    def __init__(self, worker_id, mypostgres, myredis):
        self.worker_id = worker_id
        self.db = mypostgres
        self.redis = myredis
        self._browser_required_cache: set[str] = set()  # domains needing browser
    
    async def process(self, task: dict) -> None:
        url = task["site"]
        depth = task.get("depth", 0)
        seed = task.get("seed", url)
        max_depth = task.get("max_depth", config.DEFAULT_MAX_DEPTH)
        
        domain = urlparse(url).netloc.lower()
        module_cls = get_module(domain)
        
        # Fallback: generic module
        if not module_cls:
            module_cls = BaseModule
            module_cls.domain = domain
        
        module = module_cls()
        
        # Determine transport
        soup, driver = await self._fetch_page(url, domain)
        if not soup:
            await self._handle_failure(task, "Fetch failed")
            return
        
        # Try extraction
        article_data = module.extract(soup, driver, url)
        
        if article_data:
            # It's an article → store it
            await self._store_article(article_data)
        
        # Always extract links (if depth allows)
        if depth < max_depth:
            links = module.extract_links(soup, url, seed)
            await self._enqueue_links(links, depth + 1, seed, max_depth)
    
    async def _fetch_page(self, url, domain):
        """HTTP-first with browser fallback caching."""
        # Try HTTP
        soup = await self._http_fetch(url)
        if soup and self._has_content(soup):
            self._browser_required_cache.discard(domain)
            return soup, None
        
        # HTTP failed or empty → try browser
        self._browser_required_cache.add(domain)
        return await self._browser_fetch(url)
    
    async def _store_article(self, data: ArticleData):
        """Upsert into articles, insert into crawl_history."""
        ...
    
    async def _enqueue_links(self, links, depth, seed, max_depth):
        """Dedup via Redis Set + Postgres check, then enqueue."""
        ...
    
    async def _handle_failure(self, task, reason):
        """Retry or dead-letter."""
        retries = task.get("retries", 0)
        if retries < config.DEFAULT_RETRY_COUNT:
            await self.redis.enqueue_stream(
                config.REDIS_STREAM,
                {"site": task["site"], "depth": task.get("depth", 0), 
                 "seed": task.get("seed"), "retries": retries + 1}
            )
        else:
            await self.redis.enqueue_stream(
                config.REDIS_STREAM_FAILED,
                {"site": task["site"], "reason": reason, "timestamp": ...}
            )
```

---

## Phase 3 — Crawling Pipeline

### 3.1: New Redis Message Format

Old format:
```json
{"site": "https://www.reuters.com/world/middle-east/"}
```

New format:
```json
{
  "site": "https://www.reuters.com/world/middle-east/iran-war/",
  "depth": 2,
  "seed": "https://www.reuters.com/world/",
  "max_depth": 5,
  "retries": 0
}
```

Backward compatible: `depth`, `seed`, `max_depth`, `retries` are optional with defaults.

### 3.2: Add Dedup Methods to `myredis.py`

```python
async def url_already_seen(self, url: str) -> bool:
    """Check if URL is in the seen set."""
    return await self.get_redis().sismember(config.REDIS_SEEN_SET, url)

async def mark_url_seen(self, url: str) -> None:
    """Add URL to the seen set."""
    await self.get_redis().sadd(config.REDIS_SEEN_SET, url)
```

### 3.3: Refactor `workers.py` — New Worker Loop

Current: `_workers()` creates `MyRedis`, `ModuleManager`, loops on `dequeue_stream_next`, calls `module_manager.fetch()`.

New: `_workers()` creates `MyRedis` + `MyPostgres`, passes both to `ModuleManager`. The loop calls `module_manager.process(task)` which handles the full pipeline (fetch → extract → store → discover → enqueue).

Politeness delay: `await asyncio.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))` between tasks.

### 3.4: Link Enqueue Flow

```python
async def _enqueue_links(self, links, depth, seed, max_depth):
    for link in links:
        # Check Redis dedup Set
        if await self.redis.url_already_seen(link):
            continue
        # Check Postgres (already scraped in a previous run)
        if await self._is_in_postgres(link):
            await self.redis.mark_url_seen(link)  # sync the Set
            continue
        # OK to crawl
        await self.redis.mark_url_seen(link)
        await self.redis.enqueue_stream(config.REDIS_STREAM, {
            "site": link,
            "depth": depth,
            "seed": seed,
            "max_depth": max_depth,
            "retries": 0,
        })
```

### 3.5: Dead-Letter Stream (DLQ)

Failed URLs (after retries exhausted) go to `fetcher:failed` stream:
```json
{
  "site": "https://...",
  "depth": 2,
  "seed": "https://...",
  "reason": "Extraction returned empty after 3 retries",
  "timestamp": "2026-05-15T10:30:00Z",
  "error": "..."   # exception traceback if applicable
}
```

A future `replay_failed.py` script can read this stream and re-enqueue.

### 3.6: Retry with Backoff

When `extract()` returns `None` or an exception occurs:
1. If `retries < config.DEFAULT_RETRY_COUNT` (3): re-enqueue to main stream with `retries + 1`
2. To avoid hot-looping, add `delay = config.DEFAULT_RETRY_DELAY * (2 ** retries)` — the worker sleeps this before re-enqueuing
3. After retries exhausted: push to dead-letter stream

### 3.7: Politeness Delays

Between processing each task, sleep:
```python
delay = random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX)
await asyncio.sleep(delay)
```
This is per-worker, so with 5 workers hitting the same domain, there's imperfect coordination (see Phase 4 for token-bucket upgrade).

### 3.8: Structured Logging + Periodic Counters

Log format (JSON lines):
```json
{"ts": "2026-05-15T10:30:00", "worker": 3, "event": "article_stored", "url": "https://...", "hash": "abc123"}
{"ts": "2026-05-15T10:30:02", "worker": 3, "event": "link_discovery", "url": "https://...", "links_found": 14}
{"ts": "2026-05-15T10:30:05", "worker": 3, "event": "task_failed", "url": "https://...", "retries": 1, "reason": "..."}
```

Periodic summary (every 100 tasks or 5 minutes):
```
[Worker 3] STATS: processed=142, stored=138, failed=4, links_discovered=237, running=5m32s
```

Implementation: a `WorkerStats` counter class in `workers.py` that tracks these numbers and logs summary periodically.

---

## Phase 4 — Polish

### 4.1: Dead-Letter Replay Tool

Script: `replay_failed.py` that reads `fetcher:failed` stream and re-enqueues to `fetcher` stream. Optional filters (by domain, by reason, by timestamp range).

### 4.2: Update Documentation

Update `README.md` with:
- New architecture overview
- Setup instructions (docker compose up, uv sync)
- How to add a new site module
- Configuration reference

---

## File Structure (Final)

```
core/
├── compose.yaml                 # Redis + PostgreSQL
├── plan.md                      # This file
├── .gitignore
├── AGENTS.md
├── .env                         # Secrets + config (gitignored)
├── fetcher/
│   ├── pyproject.toml           # +sqlalchemy, asyncpg, dotenv, alembic
│   ├── uv.lock
│   ├── .python-version          # 3.14
│   ├── src/
│   │   ├── config.py            # All config from env + typed constants
│   │   ├── myredis.py           # Redis pool + stream + seen_set methods
│   │   ├── mypostgres.py        # NEW: PostgreSQL pool + session management
│   │   ├── models.py            # NEW: Article, CrawlHistory ORM models
│   │   ├── main.py              # Entrypoint (minimal changes)
│   │   ├── loader.py            # Seed loader (unchanged)
│   │   ├── sites.py             # Seed URLs (unchanged)
│   │   ├── workers.py           # Worker lifecycle + stats + politeness
│   │   ├── modules/
│   │   │   ├── __init__.py      # Explicit module imports
│   │   │   ├── registry.py      # NEW: @register_module decorator
│   │   │   ├── base.py          # NEW: BaseModule + ArticleData
│   │   │   ├── services.py      # Transport layer (Fetcher/Scrapper wrappers)
│   │   │   ├── module_manager.py # Domain routing + pipeline orchestration
│   │   │   └── Reuters/
│   │   │       ├── __init__.py
│   │   │       └── module.py    # NEW: ReutersModule(BaseModule)
```

---

## Design Decision Log

| # | Decision | Choice |
|---|----------|--------|
| 1 | What to extract | Headline, Content, URL, Timestamp, content_hash |
| 2 | Link discovery method | Crawl from seed pages (loader.py provides seeds) |
| 3 | Crawl scope boundary | URL prefix matching (same prefix as seed) |
| 4 | Article vs. listing detection | Content-based: try extraction, if yields content → article, else → listing |
| 5 | Link re-enqueue + dedup | Push discovered links to Redis stream + global Redis Set for dedup + Postgres check |
| 6 | PostgreSQL schema | articles (upsert on URL) + crawl_history (append-only audit) |
| 7 | DB connection | SQLAlchemy async with asyncpg |
| 8 | Module system | @register_module decorator + explicit __init__.py imports |
| 9 | HTTP vs. browser decision | HTTP-first with per-domain browser-required cache (in-memory) |
| 10 | Crawl stopping conditions | Max depth per seed, configured in task payload |
| 11 | Config for depth | Per-seed depth in Redis message, max_pages NOT included (confusing with per-page count) |
| 12 | Data flow | Worker writes directly to Postgres + enqueues links to Redis |
| 13 | Rate limiting / politeness | Per-worker random delay between tasks (2-5s) |
| 14 | Module API | extract() returns ArticleData or None; extract_links() returns filtered URLs |
| 15 | Loader payload format | {"site": url, "depth": 3} — loader.py unchanged, depth from config |
| 16 | Extraction failure handling | Retry up to 3x with exponential backoff, then dead-letter stream |
| 17 | Postgres provisioning | Added to compose.yaml, alongside Redis |
| 18 | Table creation | DEBUG: create_all on startup; PRD: Alembic migrations |
| 19 | File structure | modules/Reuters/module.py (merged fetch+scrap), new registry.py + base.py |
| 20 | Existing code disposition | Extraction helpers moved to BaseModule; old fetch.py/scrap.py deleted |
| 21 | Depth tracking | Encoded in Redis message: {"site": url, "depth": N, "seed": "..."} |
| 22 | Content hash | SHA-256(headline + content), computed in ArticleData.from_extraction() |
| 23 | Dedup scope | Global Redis Set + Postgres UNIQUE (global, not per-seed) |
| 24 | Re-crawling | Not implemented yet; architecture supports it via future reloader.py |
| 25 | Module discovery | Explicit imports in modules/__init__.py |
| 26 | Config surface | .env for secrets, config.py for typed constants |
| 27 | Error handling / observability | Structured JSON logs + dead-letter stream + periodic counters |
| 28 | Implementation order | Phase 1 (Infra) → Phase 2 (Modules) → Phase 3 (Crawling) → Phase 4 (Polish) |
