# This is meant to run as a standalone.
# Used to initially load sites and stuff into the REDIS QUEUE.


import asyncio
import logging
from myredis import MyRedis
import sites 
import config
from logging_config import setup_logging


async def main():
    flusher = setup_logging(-1)  # loader runs as "main"
    log = logging.getLogger(__name__)
    
    log.info("Running Loader ... ")
    log.info("Connecting to Redis")
    log.info("Host: %s | Port: %s", config.REDIS_DB_HOST, config.REDIS_DB_PORT)
    myredis = MyRedis()
    if not await myredis.init_redis():
        log.critical("Couldnt connect to REDIS")
        flusher.stop()
        return

    log.info("Sending... requests on stream: %s", config.REDIS_STREAM)
    for site in sites.qualified_news:
        msg_id = await myredis.enqueue_stream(config.REDIS_STREAM, {"site":site})        
        log.info("Added Site: %s to Redis | MsgID: %s", site, msg_id, extra={"url": site})
    
    log.info("Submitted All sites to the REDIS Queue !!")
    await myredis.close_redispool()
    flusher.stop()

if __name__ == "__main__":
    asyncio.run(main())