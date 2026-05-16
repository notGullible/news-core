#!/usr/bin/env python3
"""
Reset everything — clear the Redis dedup set AND PostgreSQL article data.

Usage::

    uv run python src/reset_all.py              # full reset
    uv run python src/reset_all.py --bad-only   # only bad rows in Postgres
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

# Import the reset helpers from the sibling scripts.
from reset_postgres import main as reset_pg
from reset_redis import main as reset_redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("reset_all")


async def main(bad_only: bool) -> None:
    log.info("=== Clearing Redis dedup set ===")
    await reset_redis()

    log.info("=== Clearing PostgreSQL ===")
    await reset_pg(bad_only=bad_only)

    log.info("=== Done — pipeline is ready for a fresh crawl. ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reset Redis + Postgres data")
    parser.add_argument(
        "--bad-only",
        action="store_true",
        help="Only delete Postgres rows that look like boilerplate / 404 pages",
    )
    args = parser.parse_args()
    try:
        asyncio.run(main(bad_only=args.bad_only))
    except KeyboardInterrupt:
        log.info("Interrupted.")
        sys.exit(0)
