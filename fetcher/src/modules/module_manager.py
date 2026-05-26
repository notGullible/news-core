"""
Routes an incoming scrape task to the correct module and orchestrates
the full pipeline: fetch → extract → store → discover links → enqueue.

The :class:`ModuleManager` is the *brain* of each worker.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common import config
from modules.base import ArticleData, BaseModule, FetchContext
from modules.registry import get_module

if TYPE_CHECKING:
    from common.mypostgres import MyPostgres
    from common.myredis import MyRedis

log = logging.getLogger(__name__)

# ── structured log helper ──────────────────────────────────────────────


def _jlog(event: str, **kwargs: Any) -> None:
    """Emit a structured log line using the universal format.

    Key-value pairs become space-separated ``key=value`` tokens.
    If *kwargs* includes ``url`` it is pulled out and passed via
    ``extra`` so the formatter places it in the ``[url]`` slot.
    """
    url = kwargs.pop("url", "-")
    parts = " ".join(f"{k}={v}" for k, v in kwargs.items())
    msg = f"{event} {parts}".rstrip()
    log.info(msg, extra={"url": url})


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
            _jlog("task_skipped", reason="empty url")
            return

        domain = urlparse(url).netloc.lower() or "unknown"

        # ── Resolve module ────────────────────────────────────
        module_cls = get_module(domain) or BaseModule
        module = module_cls()
        module.domain = domain  # ensure domain is set for fallbacks

        # ── Fetch the page ────────────────────────────────────
        fetch_ctx = FetchContext(
            seed_prefix=seed,
            depth=depth,
            max_depth=max_depth,
            worker_id=self.worker_id,
        )
        soup = module.fetch(url, fetch_ctx)
        if soup is None:
            _jlog("fetch_failed", url=url, retries=retries)
            await self._handle_failure(task, "fetch returned empty")
            self._stats.failed += 1
            return

        # ── Link-aware listing detection ────────────────────
        # Check BEFORE extraction: a page with many in-scope links
        # is a listing/hub page regardless of what extract() returns.
        is_listing = (
            depth < max_depth
            and module.is_listing_page(soup, url, seed)
        )

        # ── Try extraction (article detection) ─────────────────
        if not is_listing:
            article_data = module.extract(soup, url)
            if article_data is not None:
                await self._store_article(article_data)
                self._stats.stored += 1
                _jlog("article_stored", url=url,
                      hash=article_data.content_hash[:12])
            
            # ── Always extract links (if depth allows) ─────────────
            if depth < max_depth:
                links = module.extract_links(soup, url, seed)
                self._stats.links_discovered += len(links)
                new_count = await self._enqueue_links(links, depth + 1, seed, max_depth)
                _jlog("links_summary",
                    url=url, found=len(links), enqueued=new_count, depth=depth)

        
        # ── Extract all links from this Listing Page ─────────────────
        else:
            if depth < max_depth:
                total_count = 0
                unique_count = 0
                async for link in module.extract_listings_links(soup, url, seed):
                    _jlog("listing_link_found",
                        url=url, link=link, depth=depth)
                    unique_count += await self._enqueue_links([link], depth + 1, seed, max_depth)
                    total_count += 1
                self._stats.links_discovered += total_count
                _jlog("listing_page",
                    url=url, found=total_count, enqueued=unique_count, depth=depth)
        
        self._stats.processed += 1
        await self._maybe_log_stats()

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

    # ── link discovery & enqueue ───────────────────────────────────

    async def _enqueue_links(
        self,
        links: list[str],
        depth: int,
        seed: str,
        max_depth: int,
    ) -> int:
        """Dedup links (Redis Set + Postgres), then push new ones to the stream.

        Returns the number of newly enqueued links.
        """
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
                config.REDIS_FETCHER_STREAM,
                {
                    "site": link,
                    "depth": depth,
                    "seed": seed,
                    "max_depth": max_depth,
                    "retries": 0,
                },
            )
            new_links += 1

        return new_links

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
            _jlog("task_retry", url=url, retries=retries, delay=delay)
            await asyncio.sleep(delay)
            await self.redis.enqueue_stream(
                config.REDIS_FETCHER_STREAM,
                {
                    "site": url,
                    "depth": task.get("depth", 0),
                    "seed": task.get("seed", url),
                    "max_depth": task.get("max_depth", config.DEFAULT_MAX_DEPTH),
                    "retries": retries + 1,
                },
            )
        else:
            _jlog("task_dead_letter", url=url, reason=reason)
            await self.redis.enqueue_stream(
                config.REDIS_FETCHER_STREAM_FAILED,
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
            "STATS processed=%s stored=%s failed=%s links_discovered=%s",
            self.processed,
            self.stored,
            self.failed,
            self.links_discovered,
        )
        self.tasks_since_log = 0


# Late import to avoid circular dependency at module level.
from common import models  # noqa: E402
