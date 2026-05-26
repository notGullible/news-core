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

from common import config
from common.logging_config import setup_logging
from common.mypostgres import MyPostgres
from common.myredis import MyRedis


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

    # ── Logging setup (per-process) ───────────────────────────
    flusher = setup_logging(worker_id, component="Fetcher")
    log = logging.getLogger(__name__)

    # ── Setup ─────────────────────────────────────────────────
    log.info("Connecting to Redis …")
    myredis = MyRedis()
    if not await myredis.init_redis(config.REDIS_EXTRACTOR_STREAM):
        log.critical("Redis unreachable — aborting")
        flusher.stop()
        return

    log.info("Connecting to PostgreSQL …")
    mypostgres = MyPostgres()
    if not await mypostgres.init_db():
        log.critical("PostgreSQL unreachable — aborting")
        await myredis.close_redispool()
        flusher.stop()
        return

    # **** Stuff Declared Here ****

    # ── Signal handling ───────────────────────────────────────
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGINT, main_task.cancel)  # type: ignore[arg-type]
    loop.add_signal_handler(signal.SIGTERM, main_task.cancel)  # type: ignore[arg-type]

    log.info(
        "Ready — listening on stream '%s' (group: %s, consumer: worker-%s)",
        config.REDIS_EXTRACTOR_STREAM,
        config.REDIS_STREAM_GROUP,
        worker_id,
    )

    consumer_name = f"worker-{worker_id}"

    # ── Main loop ─────────────────────────────────────────────
    try:
        while True:
            res = await myredis.dequeue_stream_next(config.REDIS_EXTRACTOR_STREAM, consumer_name)
            if not res:
                continue

            msg_id = res["msg_id"]
            data = res["data"]

            url = data.get("site", "?")
            log.info("Processing [%s] %s", msg_id, url, extra={"url": url})

            # ********** Work Done HERE **********
            # await module_manager.process(data)
            # ************************************

            # ACK: mark as processed within the consumer group.
            await myredis.ack_stream(config.REDIS_EXTRACTOR_STREAM, msg_id)

            # Politeness delay between tasks.
            delay = random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX)
            await asyncio.sleep(delay)

    except asyncio.CancelledError:
        log.info("Shutting down …")
    finally:
        await mypostgres.close_db()
        await myredis.close_redispool()
        flusher.stop()
