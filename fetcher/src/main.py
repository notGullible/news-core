import asyncio
from concurrent.futures import ProcessPoolExecutor
import logging
import multiprocessing
import signal

# My Imports
import config 
from workers import start_workers


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger(__name__)

async def main():
    log.info("NG Fetcher starting …")
    log.info("Redis : %s:%s  |  Stream : %s", config.REDIS_DB_HOST, config.REDIS_DB_PORT, config.REDIS_STREAM)
    log.info("PG   : %s:%s/%s", config.POSTGRES_HOST, config.POSTGRES_PORT, config.POSTGRES_DB)
    log.info("Workers : %s  |  Debug : %s", config.NUMBER_OF_WORKERS, config.DEBUG)
   
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGINT, main_task.cancel) # type: ignore
    loop.add_signal_handler(signal.SIGTERM, main_task.cancel) # type: ignore
    
    log.info(f"Listening to incoming requests on stream: {config.REDIS_STREAM}")
    
    pool = ProcessPoolExecutor(
        max_workers=config.NUMBER_OF_WORKERS,
        mp_context=multiprocessing.get_context("spawn")
    )
    try:
        await start_workers(pool)
    except asyncio.CancelledError:
        log.info("Shutting down...")
    finally:
        pool.shutdown(wait=True)

if __name__ == "__main__":
    asyncio.run(main())
    # main()