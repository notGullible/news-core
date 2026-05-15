# Handles workers, creation and management
import asyncio
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import ThreadPoolExecutor
import logging
from typing import Union
import signal

import config
from modules.module_manager import ModuleManager
from myredis import MyRedis

log = logging.getLogger(__name__)

# Let the Caller Set the params
async def start_workers(PoolExecutor:Union[ProcessPoolExecutor, ThreadPoolExecutor]):   
    loop = asyncio.get_running_loop()
    futures = [PoolExecutor.submit(_call_workers, i) for i in range(config.NUMBER_OF_WORKERS)]
    awaitables = [asyncio.wrap_future(f, loop=loop) for f in futures]
    await asyncio.gather(*awaitables)  # blocks forever (workers are infinite loops)

def stop_workers():
    # TODO: I dont think we need it ? SIGTERM is sent.
    # But need it for when errors are generated
    pass

def _call_workers(worker_id:int):    
    asyncio.run(_workers(worker_id))

async def _workers(worker_id:int):
    # TODO: Modify the logging config to auto : "   %(asctime)s | %(message)s | [{worker_id}] | "
    # No need for " [Fetcher][{working_id}] ..."
    # logging.basicConfig(level=logging.INFO, format=f"   %(asctime)s | %(message)s | [{worker_id}] | ")

    log.info(f"  [Fetcher][{worker_id}] !! Connecting to the REDIS Server")
    log.info(f"  [Fetcher][{worker_id}] !! Host: {config.REDIS_DB_HOST} | Port: {config.REDIS_DB_PORT}")
    myredis = MyRedis()
    if not await myredis.init_redis():
        log.critical(f"  [Fetcher][{worker_id}] Couldnt connect to REDIS")
        return
    
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGINT, main_task.cancel) # type: ignore
    loop.add_signal_handler(signal.SIGTERM, main_task.cancel) # type: ignore
    
    log.info(f"  [Fetcher][{worker_id}] Connected to Redis (pool: {config.REDIS_POOL_MIN}-{config.REDIS_POOL_MAX} connections)")
    log.info(f"  [Fetcher][{worker_id}] Listening to incoming requests on stream: {config.REDIS_STREAM}")
    
    module_manager = ModuleManager(worker_id=worker_id)

    try:
        while True:
            res = await myredis.dequeue_stream_next(config.REDIS_STREAM)
            if res:
                await myredis.delete_msg_stream(config.REDIS_STREAM, res["msg_id"])
                log.info(f"  [Fetcher][{worker_id}] Processing {res['msg_id']}: {res['data']}")
                output = module_manager.fetch(res['data'])
                log.info(f"  [Fetcher][{worker_id}] Done {res['msg_id']} | Output:{output}")
    except asyncio.CancelledError:
        log.info("Shutting down...")
    finally:
        await myredis.close_redispool() # Nukes the entire connection pool
