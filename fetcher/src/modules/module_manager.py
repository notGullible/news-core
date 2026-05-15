"""
Routes an incoming scrape task to the correct module and orchestrates
the full pipeline: fetch → extract → store → discover links → enqueue.

The :class:`ModuleManager` is the *brain* of each worker.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

import config
from modules.base import ArticleData, BaseModule
from modules.registry import get_module
from modules.services import browser_fetch, http_fetch

if TYPE_CHECKING:
    from mypostgres import MyPostgres
    from myredis import MyRedis

log = logging.getLogger(__name__)

# ── structured log helper ──────────────────────────────────────────────


def _jlog(worker_id: int, event: str, **kwargs: Any) -> None:
    """Emit a JSON-structured log line for machine parsing."""
    payload = json.dumps({"ts": None, "worker": worker_id, "event": event, **kwargs}, default=str)
    log.info(payload)


# ── ModuleManager ──────────────────────────────────────────────────────


class ModuleManager:
    def __init__(
        self,
        worker_id: int,
        myredis: MyRedis,
        mypostgres: MyPostgres,
    ) -> None:
        self.worker_id = worker_id
        self.redis = myredis
        self.db = mypostgres
        # In-memory cache: domains that we've confirmed require a browser.
        self._browser_required_cache: set[str] = set()
        # Stats (see periodic summary)
        self._stats = WorkerStats()

    # ── main entry point ──────────────────────────────────────────

    async def process(self, task: dict[str, Any]) -> None:
        """Run the full pipeline for a single de-queued *task*."""
        url: str = task.get("site", "")
        depth: int = int(task.get("depth", 0))
        seed: str = task.get("seed", url)
        max_depth: int = int(task.get("max_depth", config.DEFAULT_MAX_DEPTH))
        retries: int = int(task.get("retries", 0))

        if not url:
            _jlog(self.worker_id, "task_skipped", reason="empty url")
            return

        domain = urlparse(url).netloc.lower() or "unknown"

        # ── Resolve module ────────────────────────────────────
        module_cls = get_module(domain) or BaseModule
        module = module_cls()
        module.domain = domain  # ensure domain is set for fallbacks

        # ── Fetch the page ────────────────────────────────────
        soup = await self._fetch_page(url, domain)
        if soup is None:
            _jlog(self.worker_id, "fetch_failed", url=url, retries=retries)
            await self._handle_failure(task, "fetch returned empty")
            self._stats.failed += 1
            return

        # ── Try extraction (article detection) ─────────────────
        article_data = module.extract(soup, url)
        if article_data is not None:
            await self._store_article(article_data)
            self._stats.stored += 1

        # ── Always extract links (if depth allows) ─────────────
        if depth < max_depth:
            links = module.extract_links(soup, url, seed)
            self._stats.links_discovered += len(links)
            await self._enqueue_links(links, depth + 1, seed, max_depth)

        self._stats.processed += 1
        await self._maybe_log_stats()

    # ── fetch (HTTP-first with browser cache) ─────────────────────

    async def _fetch_page(self, url: str, domain: str):
        """HTTP-first; fall back to headless browser if needed (cached per domain)."""
        # 1 — Try HTTP if we haven't cached this domain as browser-only.
        if domain not in self._browser_required_cache:
            soup = http_fetch(url)
            if soup is not None and self._has_meaningful_content(soup):
                return soup

        # 2 — Browser fallback.
        self._browser_required_cache.add(domain)
        return browser_fetch(url, self.worker_id)

    @staticmethod
    def _has_meaningful_content(soup) -> bool:
        """Quick heuristic: does the page contain at least one <p> or <h1>?"""
        return bool(soup.find("p") or soup.find("h1"))

    # ── PostgreSQL persistence ────────────────────────────────────

    async def _store_article(self, data: ArticleData) -> None:
        """Upsert into ``articles`` and always append to ``crawl_history``."""
        async with self.db.get_session() as session:
            # 1 — Upsert the article row.
            stmt = (
                pg_insert(models.Article)  # type: ignore[attr-defined]
                .values(
                    url=data.url,
                    source_domain=data.source_domain,
                    headline=data.headline,
                    content=data.content,
                    content_hash=data.content_hash,
                )
                .on_conflict_do_update(
                    index_elements=["url"],
                    set_={
                        "headline": data.headline,
                        "content": data.content,
                        "content_hash": data.content_hash,
                        "updated_at": text("NOW()"),
                    },
                )
                .returning(models.Article.id)  # type: ignore[attr-defined]
            )
            result = await session.execute(stmt)
            row = result.fetchone()
            article_id = row[0] if row else None

            # 2 — Append crawl history.
            session.add(
                models.CrawlHistory(  # type: ignore[attr-defined]
                    article_id=article_id,
                    url=data.url,
                    content_hash=data.content_hash,
                )
            )
            await session.commit()

        _jlog(
            self.worker_id,
            "article_stored",
            url=data.url,
            hash=data.content_hash[:12],
        )

    # ── link discovery & enqueue ───────────────────────────────────

    async def _enqueue_links(
        self,
        links: list[str],
        depth: int,
        seed: str,
        max_depth: int,
    ) -> None:
        """Dedup links (Redis Set + Postgres), then push new ones to the stream."""
        new_links = 0
        for link in links:
            # 1 — Redis Set dedup (fast path).
            if await self.redis.url_already_seen(link):
                continue

            # 2 — Postgres dedup (catches URLs from previous runs).
            if await self._url_in_postgres(link):
                await self.redis.mark_url_seen(link)
                continue

            # 3 — OK, enqueue.
            await self.redis.mark_url_seen(link)
            await self.redis.enqueue_stream(
                config.REDIS_STREAM,
                {
                    "site": link,
                    "depth": depth,
                    "seed": seed,
                    "max_depth": max_depth,
                    "retries": 0,
                },
            )
            new_links += 1

        if new_links:
            _jlog(self.worker_id, "links_enqueued", count=new_links, depth=depth)

    async def _url_in_postgres(self, url: str) -> bool:
        """Check whether *url* exists in the ``articles`` table."""
        async with self.db.get_session() as session:
            stmt = select(models.Article.id).where(  # type: ignore[attr-defined]
                models.Article.url == url  # type: ignore[attr-defined]
            )
            result = await session.execute(stmt)
            return result.first() is not None

    # ── failure handling ───────────────────────────────────────────

    async def _handle_failure(self, task: dict[str, Any], reason: str) -> None:
        """Retry with exponential backoff, or send to dead-letter stream."""
        retries: int = int(task.get("retries", 0))
        url: str = task.get("site", "")

        if retries < config.DEFAULT_RETRY_COUNT:
            delay = config.DEFAULT_RETRY_DELAY * (2**retries)
            _jlog(self.worker_id, "task_retry", url=url, retries=retries, delay=delay)
            await asyncio.sleep(delay)
            await self.redis.enqueue_stream(
                config.REDIS_STREAM,
                {
                    "site": url,
                    "depth": task.get("depth", 0),
                    "seed": task.get("seed", url),
                    "max_depth": task.get("max_depth", config.DEFAULT_MAX_DEPTH),
                    "retries": retries + 1,
                },
            )
        else:
            _jlog(self.worker_id, "task_dead_letter", url=url, reason=reason)
            await self.redis.enqueue_stream(
                config.REDIS_STREAM_FAILED,
                {
                    "site": url,
                    "depth": task.get("depth", 0),
                    "seed": task.get("seed", url),
                    "reason": reason,
                    "retries_exhausted": retries,
                    "timestamp": str(asyncio.get_event_loop().time()),
                },
            )

    # ── periodic stats ──────────────────────────────────────────────

    async def _maybe_log_stats(self) -> None:
        """Emit a summary line every N tasks or M seconds."""
        self._stats.tasks_since_log += 1
        if self._stats.tasks_since_log >= 100:
            self._stats.log(self.worker_id)


# ── WorkerStats helper ──────────────────────────────────────────────────


class WorkerStats:
    def __init__(self) -> None:
        self.processed = 0
        self.stored = 0
        self.failed = 0
        self.links_discovered = 0
        self.tasks_since_log = 0

    def log(self, worker_id: int) -> None:
        log.info(
            "[Worker %s] STATS: processed=%s, stored=%s, failed=%s, links_discovered=%s",
            worker_id,
            self.processed,
            self.stored,
            self.failed,
            self.links_discovered,
        )
        self.tasks_since_log = 0


# Late import to avoid circular dependency at module level.
import models  # noqa: E402
