"""
Worker lifecycle — spawns N async worker tasks, each running an infinite
loop that dequeues tasks from Redis, processes them through the
:class:`ModuleManager`, and re-enqueues discovered links.

Each worker:
* Shares Redis and PostgreSQL connection pools (single-process).
* Creates a :class:`ModuleManager` that owns the full pipeline.
* Sleeps a random politeness delay between tasks.
* Handles graceful shutdown via :class:`asyncio.CancelledError`.
"""

from __future__ import annotations

import asyncio
import logging

from logging_config import setup_logging
import config
from modules.module_manager import ModuleManager
from mypostgres import MyPostgres
from myredis import MyRedis


class _WorkerLogAdapter(logging.LoggerAdapter):
    """Injects ``worker_id`` into every log record's ``extra`` dict.

    The :class:`~logging_config.FetcherFormatter` reads ``worker_id``
    from the record to build the ``[Grouper][N]`` prefix.
    """

    def process(self, msg, kwargs):
        kwargs["extra"] = kwargs.get("extra", {})
        kwargs["extra"]["worker_id"] = self.extra["worker_id"]
        return msg, kwargs 


# ── public entry point ─────────────────────────────────────────────────
async def start_workers(
    myredis: MyRedis,
    mypostgres: MyPostgres,
) -> None:
    """Launch *config.NUMBER_OF_WORKERS* async workers and wait forever."""
    tasks = [
        asyncio.create_task(_worker(i, myredis, mypostgres))
        for i in range(config.NUMBER_OF_WORKERS)
    ]
    await asyncio.gather(*tasks)


# ── internal helpers ───────────────────────────────────────────────────

async def _worker(
    worker_id: int,
    myredis: MyRedis,
    mypostgres: MyPostgres,
) -> None:
    """Infinite loop: dequeue → process → repeat."""
    
    base_log = logging.getLogger(__name__)                                                                                                                                                                    
    log = _WorkerLogAdapter(base_log, {"worker_id": worker_id})  
    
    module_manager = ModuleManager(
        worker_id=worker_id,
        myredis=myredis,
        mypostgres=mypostgres,
    )

    consumer_name = f"worker-{worker_id}"

    log.info(
        "Ready — listening on stream '%s' (group: %s, consumer: %s)",
        config.REDIS_STREAM,
        config.REDIS_STREAM_GROUP,
        consumer_name,
    )

    try:
        while True:
            res = await myredis.dequeue_stream_next(
                config.REDIS_STREAM, consumer_name
            )
            if not res:
                continue

            msg_id = res["msg_id"]
            data = res["data"]

            url = data.get("site", "?")
            log.info("Processing [%s] %s", msg_id, url, extra={"url": url})

            await module_manager.process(data)

            # ACK: mark as processed within the consumer group.
            await myredis.ack_stream(config.REDIS_STREAM, msg_id)


    except asyncio.CancelledError:
        log.info("Worker %d shutting down …", worker_id)