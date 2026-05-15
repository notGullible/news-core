"""
Worker lifecycle — spawns N subprocess workers, each running an infinite
async loop that dequeues tasks from Redis, processes them through the
:class:`ModuleManager`, and re-enqueues discovered links.

Each worker:
* Connects to Redis and PostgreSQL on startup.
* Creates a :class:`ModuleManager` that owns the full pipeline.
* Sleeps a random politeness delay between tasks.
* Handles graceful shutdown via SIGINT / SIGTERM.
"""

from __future__ import annotations

import asyncio
import logging
import random
import signal
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Union

import config
from modules.module_manager import ModuleManager
from mypostgres import MyPostgres
from myredis import MyRedis

log = logging.getLogger(__name__)


# ── public entry point ─────────────────────────────────────────────────


async def start_workers(
    pool_executor: Union[ProcessPoolExecutor, ThreadPoolExecutor],
) -> None:
    """Launch *config.NUMBER_OF_WORKERS* workers and wait forever."""
    loop = asyncio.get_running_loop()
    futures = [
        pool_executor.submit(_call_worker, i)
        for i in range(config.NUMBER_OF_WORKERS)
    ]
    awaitables = [asyncio.wrap_future(f, loop=loop) for f in futures]
    await asyncio.gather(*awaitables)  # blocks forever


# ── internal helpers ───────────────────────────────────────────────────


def _call_worker(worker_id: int) -> None:
    """Entry-point for each subprocess — boots the asyncio loop."""
    asyncio.run(_worker(worker_id))


async def _worker(worker_id: int) -> None:
    """Infinite loop: connect → dequeue → process → repeat."""

    # ── Setup ─────────────────────────────────────────────────
    log.info("  [Fetcher][%s] Connecting to Redis …", worker_id)
    myredis = MyRedis()
    if not await myredis.init_redis():
        log.critical("  [Fetcher][%s] Redis unreachable — aborting", worker_id)
        return

    log.info("  [Fetcher][%s] Connecting to PostgreSQL …", worker_id)
    mypostgres = MyPostgres()
    if not await mypostgres.init_db():
        log.critical("  [Fetcher][%s] PostgreSQL unreachable — aborting", worker_id)
        await myredis.close_redispool()
        return

    module_manager = ModuleManager(
        worker_id=worker_id,
        myredis=myredis,
        mypostgres=mypostgres,
    )

    # ── Signal handling ───────────────────────────────────────
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGINT, main_task.cancel)  # type: ignore[arg-type]
    loop.add_signal_handler(signal.SIGTERM, main_task.cancel)  # type: ignore[arg-type]

    log.info(
        "  [Fetcher][%s] Ready — listening on stream '%s' (group: %s, consumer: worker-%s)",
        worker_id,
        config.REDIS_STREAM,
        config.REDIS_STREAM_GROUP,
        worker_id,
    )

    consumer_name = f"worker-{worker_id}"

    # ── Main loop ─────────────────────────────────────────────
    try:
        while True:
            res = await myredis.dequeue_stream_next(config.REDIS_STREAM, consumer_name)
            if not res:
                continue

            msg_id = res["msg_id"]
            data = res["data"]

            url = data.get("site", "?")
            log.info("  [Fetcher][%s] Processing [%s] %s", worker_id, msg_id, url)

            await module_manager.process(data)

            # ACK: mark as processed within the consumer group.
            await myredis.ack_stream(config.REDIS_STREAM, msg_id)

            # Politeness delay between tasks.
            delay = random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX)
            await asyncio.sleep(delay)

    except asyncio.CancelledError:
        log.info("  [Fetcher][%s] Shutting down …", worker_id)
    finally:
        await mypostgres.close_db()
        await myredis.close_redispool()
