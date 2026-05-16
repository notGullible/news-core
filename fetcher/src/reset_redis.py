#!/usr/bin/env python3
"""
Clear the Redis deduplication set so previously-seen URLs can be re-scraped.

Usage::

    uv run python src/reset_redis.py

The script connects to Redis using the project's ``.env`` / ``config.py``
settings and deletes the ``REDIS_SEEN_SET`` key.
"""

from __future__ import annotations

import asyncio
import logging
import sys

import config
from myredis import MyRedis

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("reset_redis")


async def main() -> None:
    redis = MyRedis()
    if not await redis.init_redis():
        log.critical("Cannot connect to Redis — aborting.")
        sys.exit(1)

    client = redis.get_redis()
    deleted = await client.delete(config.REDIS_SEEN_SET)  # type: ignore[no-untyped-call]
    log.info("Deleted key '%s' (%s keys removed).", config.REDIS_SEEN_SET, deleted)
    await redis.close_redispool()


if __name__ == "__main__":
    asyncio.run(main())
