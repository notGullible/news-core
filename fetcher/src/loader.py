# This is meant to run as a standalone.
# Used to initially load sites and stuff into the REDIS QUEUE.


import asyncio
import logging
from myredis import MyRedis
import sites 
import config

log = logging.getLogger(__name__)

async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
    
    log.info("Running Loader ... ")
    log.info("Connecting to Redis")
    log.info(f"!! Host: {config.REDIS_DB_HOST} | Port: {config.REDIS_DB_PORT}")
    myredis = MyRedis()
    if not await myredis.init_redis():
        log.critical("Couldnt connect to REDIS")
        return

    log.info(f"Sending... requests on stream: {config.REDIS_STREAM}")
    for site in sites.qualified_news:
        msg_id = await myredis.enqueue_stream(config.REDIS_STREAM, {"site":site})        
        log.info(f"✅ Added Site: {site} to Redis Key:Fetcher Successful | MsgID: {msg_id}")
    
    log.info("Submitted All sites to the REDIS Queue !!")
    await myredis.close_redispool()

if __name__ == "__main__":
    asyncio.run(main())