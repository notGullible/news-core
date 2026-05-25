import asyncio
import logging
import signal

# My Imports
import config
from logging_config import setup_logging
from mypostgres import MyPostgres
from myredis import MyRedis
from workers import start_workers


async def main():
    flusher = setup_logging(-1)  # main process (worker_id=-1)
    log = logging.getLogger(__name__)

    log.info("NG Grouper starting …")
    log.info("Redis : %s:%s  |  Stream : %s", config.REDIS_DB_HOST, config.REDIS_DB_PORT, config.REDIS_STREAM)
    log.info("PG   : %s:%s/%s", config.POSTGRES_HOST, config.POSTGRES_PORT, config.POSTGRES_DB)
    log.info("Workers : %s  |  Debug : %s  |  Log level : %s", config.NUMBER_OF_WORKERS, config.DEBUG, config.LOG_LEVEL)

    # ── Shared infrastructure ─────────────────────────────────
    myredis = MyRedis()
    if not await myredis.init_redis():
        log.critical("Redis unreachable — aborting")
        flusher.stop()
        return

    mypostgres = MyPostgres()
    if not await mypostgres.init_db():
        log.critical("PostgreSQL unreachable — aborting")
        await myredis.close_redispool()
        flusher.stop()
        return

    # ── Signal handling ───────────────────────────────────────
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGINT, main_task.cancel)  # type: ignore[arg-type]
    loop.add_signal_handler(signal.SIGTERM, main_task.cancel)  # type: ignore[arg-type]

    log.info("Starting %d async workers on stream: %s", config.NUMBER_OF_WORKERS, config.REDIS_STREAM)

    try:
        await start_workers(myredis, mypostgres)
    except asyncio.CancelledError:
        log.info("Shutting down …")
    finally:
        await mypostgres.close_db()
        await myredis.close_redispool()
        flusher.stop()

if __name__ == "__main__":
    asyncio.run(main())