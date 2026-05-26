#!/usr/bin/env python3
"""
Replay failed tasks from the dead-letter stream back into the main fetcher stream.

Usage::

    uv run python replay_failed.py              # replay everything
    uv run python replay_failed.py --domain reuters.com  # filter by domain
    uv run python replay_failed.py --dry-run     # just print what would be replayed

This reads the ``REDIS_STREAM_FAILED`` stream, optionally filters entries,
and re-enqueues them into ``REDIS_STREAM``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from urllib.parse import urlparse

from common import config
from common.logging_config import setup_logging
from common.myredis import MyRedis


log = logging.getLogger("replay_failed")
async def main(dry_run: bool, domain_filter: str | None) -> None:
    flusher = setup_logging(-1, component="Fetcher")

    redis = MyRedis()
    if not await redis.init_redis(config.REDIS_FETCHER_STREAM_FAILED):
        log.critical("Cannot connect to Redis — aborting.")
        return

    replay_count = 0
    skip_count = 0

    # XREAD all pending entries (non-blocking).
    while True:
        entry = await redis._xread_one(config.REDIS_FETCHER_STREAM_FAILED)
        if not entry:
            break

        msg_id = entry["msg_id"]
        data: dict = entry["data"]

        url = data.get("site", "")
        domain = urlparse(url).netloc.lower()

        # Optional domain filter.
        if domain_filter and domain not in domain_filter:
            skip_count += 1
            await redis.delete_msg_stream(config.REDIS_FETCHER_STREAM_FAILED, msg_id)
            continue

        # Build a fresh task (reset retries, depth, defaults).
        task = {
            "site": url,
            "depth": 0,
            "seed": url,
            "max_depth": config.DEFAULT_MAX_DEPTH,
            "retries": 0,
        }

        if dry_run:
            log.info("  [DRY-RUN] would replay: %s", url)
        else:
            await redis.enqueue_stream(config.REDIS_FETCHER_STREAM, task)
            log.info("  Replayed: %s", url)

        await redis.delete_msg_stream(config.REDIS_FETCHER_STREAM_FAILED, msg_id)
        replay_count += 1

    log.info(
        "Done — replayed %s tasks, skipped %s (domain filter).",
        replay_count,
        skip_count,
    )
    await redis.close_redispool()
    flusher.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay dead-letter tasks")
    parser.add_argument("--dry-run", action="store_true", help="Print only, do not enqueue")
    parser.add_argument("--domain", type=str, help="Only replay tasks from this domain")
    args = parser.parse_args()

    try:
        asyncio.run(main(dry_run=args.dry_run, domain_filter=args.domain))
    except KeyboardInterrupt:
        log.info("Interrupted.")
        sys.exit(0)
