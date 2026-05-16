#!/usr/bin/env python3
"""
Clear article data from PostgreSQL.

By default, truncates *all* rows from ``articles`` and ``crawl_history``.
Pass ``--bad-only`` to only delete rows that look like boilerplate / 404 pages
(empty or very short content, or content matching known boilerplate patterns).

Usage::

    uv run python src/reset_postgres.py              # truncate everything
    uv run python src/reset_postgres.py --bad-only   # only delete bad rows
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import delete, select, text

import config
from models import Article, CrawlHistory
from mypostgres import MyPostgres

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("reset_postgres")


async def main(bad_only: bool) -> None:
    db = MyPostgres()
    if not await db.init_db():
        log.critical("Cannot connect to PostgreSQL — aborting.")
        sys.exit(1)

    async with db.get_session() as session:
        if bad_only:
            # Remove rows that are clearly boilerplate / 404s.
            bad_patterns = [
                Article.content.like("%Our Standards%"),
                Article.content.like("%Sign up%"),
                Article.content == None,  # noqa: E711
                Article.headline.like("We can't find%"),
                Article.headline == None,  # noqa: E711
            ]

            # Build OR conditions.
            from sqlalchemy import or_

            condition = or_(*bad_patterns)

            # Find matching article IDs first.
            result = await session.execute(select(Article.id).where(condition))
            bad_ids = [row[0] for row in result.fetchall()]
            bad_urls_result = await session.execute(
                select(Article.url).where(condition)
            )
            bad_urls = [row[0] for row in bad_urls_result.fetchall()]

            if not bad_ids:
                log.info("No bad rows found — nothing to delete.")
                return

            # Delete crawl_history for those articles first (FK).
            await session.execute(
                delete(CrawlHistory).where(CrawlHistory.article_id.in_(bad_ids))
            )
            # Delete the articles.
            await session.execute(delete(Article).where(Article.id.in_(bad_ids)))
            await session.commit()

            log.info("Deleted %s bad article(s):", len(bad_ids))
            for url in bad_urls:
                log.info("  %s", url)

        else:
            # Full truncate.
            await session.execute(text("TRUNCATE articles, crawl_history RESTART IDENTITY"))
            await session.commit()
            log.info("Truncated articles + crawl_history (both tables now empty).")

    await db.close_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clear Postgres article data")
    parser.add_argument(
        "--bad-only",
        action="store_true",
        help="Only delete rows that look like boilerplate / 404 pages",
    )
    args = parser.parse_args()
    asyncio.run(main(bad_only=args.bad_only))
