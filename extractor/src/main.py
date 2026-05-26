import asyncio
from concurrent.futures import ProcessPoolExecutor
import logging
import multiprocessing
import signal

# My Imports
from common import config
from common.logging_config import setup_logging
from workers import start_workers
from common.myembeddings import MyEmbeddings

async def main():
    flusher = setup_logging(-1, component="Extractor")
    log = logging.getLogger(__name__)

    log.info("NG Extractor starting …")
    log.info("Redis : %s:%s  |  Stream : %s", config.REDIS_DB_HOST, config.REDIS_DB_PORT, config.REDIS_EXTRACTOR_STREAM)
    log.info("PG   : %s:%s/%s", config.POSTGRES_HOST, config.POSTGRES_PORT, config.POSTGRES_DB)
    log.info("Workers : %s  |  Debug : %s  |  Log level : %s", config.NUMBER_OF_WORKERS, config.DEBUG, config.LOG_LEVEL)
    log.info("Loading Embedding Model, to install if not;  %s", config.EMBEDDING_MODEL_NAME)
    MyEmbeddings().release_embedding_model()

   
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGINT, main_task.cancel) # type: ignore
    loop.add_signal_handler(signal.SIGTERM, main_task.cancel) # type: ignore
    
    log.info("Listening to incoming requests on stream: %s", config.REDIS_EXTRACTOR_STREAM)
    
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
        flusher.stop()

if __name__ == "__main__":
    asyncio.run(main())