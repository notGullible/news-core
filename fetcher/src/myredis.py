from typing import Any, Dict, cast
import redis.asyncio as aioredis
import logging
import asyncio
import config as config
from redis.typing import EncodableT, FieldT

log = logging.getLogger(__name__)

class MyRedis:
    _redis_pool = aioredis.ConnectionPool(
        host=config.REDIS_DB_HOST,
        port=config.REDIS_DB_PORT,
        decode_responses=True,
        max_connections=config.REDIS_POOL_MAX,
        retry_on_timeout=True,
        health_check_interval=30,
    )

    def get_redis(self) -> aioredis.Redis:
        """Factory: returns a Redis client drawing from the shared pool.
        Analogous to DBAsyncSession() in the SQLAlchemy pattern.
        Each call returns a thin Redis wrapper — connections are managed
        by the pool, not by this object.
        """
        return aioredis.Redis(connection_pool=self._redis_pool)

    async def init_redis(self) -> bool:
        """Verify the pool connects and pre-warm minConn connections.
        Analogous to init_db() in the SQLAlchemy pattern.
        """
    
        client = self.get_redis()
        try:
            await client.ping() # type: ignore

            async def _ping():
                return await client.ping() # type: ignore
               
            await asyncio.gather(*[_ping() for _ in range(config.REDIS_POOL_MIN)])
            return True
        except Exception:
            await self._redis_pool.aclose()
            return False


    async def close_redispool(self):
        """Gracefully shut down the pool. Call on app shutdown."""
        if self._redis_pool:
            try:
                await self._redis_pool.aclose()
                log.info("Closed Redis connection pool")
            except Exception:
                log.warning("Could not close Redis connection pool")
                pass

    async def enqueue_stream(self, stream_name: str, payload: dict[str, Any]) -> str:
        client = self.get_redis()

        try:
            payload_ = cast(Dict[FieldT, EncodableT], payload)
            msg_id = await client.xadd(stream_name, payload_)
            log.info(f"Published [{msg_id}]: {payload}")
            return str(msg_id)
        
        except Exception as e:
            log.warning(f"Could not publish message: {payload} as, Payload not in proper format. Error: {e}.")
            return str("NONE")


    async def dequeue_stream_next(self, stream_name: str) -> dict[str, Any] | None:
        client = self.get_redis()
        result = await client.xread({stream_name: "0-0"}, count=1, block=0)
        if not result:
            return None
        _stream_name, messages = result[0]
        msg_id, data = messages[0]
        return {"msg_id": msg_id, "data": data}


    async def delete_msg_stream(self,  stream_name: str, id_: Any):
        client = self.get_redis()
        await client.xdel(stream_name, id_)