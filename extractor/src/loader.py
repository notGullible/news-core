"""Extractor loader — pushes articles from PostgreSQL into the extractor Redis queue.

Reads every article that the fetcher has stored in the ``articles`` table
and enqueues it onto a dedicated Redis stream (``extractor``) for the
extractor workers to pick up, embed, and store in Qdrant.

Usage::

    cd extractor && uv run python loader.py
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from common import config
from common.logging_config import setup_logging
from common.models import Article
from common.mypostgres import MyPostgres
from common.myredis import MyRedis


async def main() -> None:
    flusher = setup_logging(-1, component="Extractor")
    log = logging.getLogger(__name__)

    log.info("Extractor Loader starting …")

    # ── Connect to PostgreSQL ─────────────────────────────────
    log.info(
        "Connecting to PostgreSQL — %s:%s/%s",
        config.POSTGRES_HOST,
        config.POSTGRES_PORT,
        config.POSTGRES_DB,
    )
    mypostgres = MyPostgres()
    if not await mypostgres.init_db():
        log.critical("PostgreSQL unreachable — aborting")
        flusher.stop()
        return

    # ── Connect to Redis ──────────────────────────────────────
    log.info(
        "Connecting to Redis — %s:%s",
        config.REDIS_DB_HOST,
        config.REDIS_DB_PORT,
    )
    myredis = MyRedis()
    if not await myredis.init_redis(config.REDIS_EXTRACTOR_STREAM):
        log.critical("Redis unreachable — aborting")
        await mypostgres.close_db()
        flusher.stop()
        return

    # ── Query articles from PostgreSQL ────────────────────────
    log.info("Fetching articles from PostgreSQL …")
    async with mypostgres.get_session() as session:
        result = await session.execute(
            select(Article.id, Article.url, Article.source_domain) # type: ignore
            .order_by(Article.id)
        )
        rows = result.fetchall()

    if not rows:
        log.info("No articles found in PostgreSQL — nothing to enqueue.")
        await myredis.close_redispool()
        await mypostgres.close_db()
        flusher.stop()
        return

    log.info("Found %d article(s) to enqueue", len(rows))

    # ── Enqueue each article into the extractor Redis stream ──
    log.info("Publishing to stream '%s' …", config.REDIS_EXTRACTOR_STREAM)
    for article_id, url, source_domain in rows:
        msg_id = await myredis.enqueue_stream(
            config.REDIS_EXTRACTOR_STREAM,
            {
                "article_id": str(article_id),
                "url": url,
                "source_domain": source_domain,
            },
        )
        log.info(
            "Enqueued article %s — %s",
            article_id,
            url,
            extra={"url": url},
        )

    log.info(
        "Done — %d article(s) published to stream '%s'",
        len(rows),
        config.REDIS_EXTRACTOR_STREAM,
    )

    await myredis.close_redispool()
    await mypostgres.close_db()
    flusher.stop()


if __name__ == "__main__":
    asyncio.run(main())