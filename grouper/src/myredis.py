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
        """Verify the pool connects, pre-warm connections, and ensure the
        consumer group exists on the main stream."""
        client = self.get_redis()
        try:
            await client.ping() # type: ignore

            async def _ping():
                return await client.ping() # type: ignore
               
            await asyncio.gather(*[_ping() for _ in range(config.REDIS_POOL_MIN)])

            # Create the consumer group (idempotent — MKSTREAM creates
            # the stream if needed, and the try/except handles the case
            # where the group already exists).
            try:
                await client.xgroup_create(  # type: ignore
                    config.REDIS_STREAM,
                    config.REDIS_STREAM_GROUP,
                    id="0",
                    mkstream=True,
                )
            except Exception:
                pass  # group already exists — that's fine.

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
            log.info("Published [%s]: %s", msg_id, payload)
            return str(msg_id)
        
        except Exception as e:
            log.warning("Could not publish message: %s. Error: %s", payload, e)
            return str("NONE")


    async def dequeue_stream_next(self, stream_name: str, consumer_name: str) -> dict[str, Any] | None:
        """Read one message from *stream_name* via the consumer group.

        Uses ``XREADGROUP`` so that each message is delivered to exactly
        one consumer within the group.
        """
        client = self.get_redis()
        result = await client.xreadgroup(  # type: ignore
            groupname=config.REDIS_STREAM_GROUP,
            consumername=consumer_name,
            streams={stream_name: ">"},
            count=1,
            block=0,
        )
        if not result:
            return None
        _stream_name, messages = result[0]
        msg_id, data = messages[0]
        return {"msg_id": msg_id, "data": data}


    async def ack_stream(self, stream_name: str, msg_id: Any) -> None:
        """Acknowledge *msg_id* in the consumer group so it is never redelivered."""
        client = self.get_redis()
        await client.xack(stream_name, config.REDIS_STREAM_GROUP, msg_id)  # type: ignore


    async def delete_msg_stream(self,  stream_name: str, id_: Any):
        """Remove *id_* from *stream_name* entirely (not group-aware).

        Prefer :meth:`ack_stream` for normal message acknowledgment —
        this is kept for dead-letter cleanup where we want the message
        physically removed.
        """
        client = self.get_redis()
        await client.xdel(stream_name, id_)

    # ── plain xread (for dead-letter replay tool) ───────

    async def _xread_one(self, stream_name: str) -> dict[str, Any] | None:
        """Non-group-aware read of one message (used by replay tool for
        dead-letter stream inspection)."""
        client = self.get_redis()
        result = await client.xread({stream_name: "0-0"}, count=1, block=100)  # type: ignore
        if not result:
            return None
        _sn, messages = result[0]
        msg_id, data = messages[0]
        return {"msg_id": msg_id, "data": data}

    # ── deduplication helpers ──────────────────────────────

    async def url_already_seen(self, url: str) -> bool:
        """Return True if *url* is already in the global seen-URLs Set."""
        client = self.get_redis()
        return bool(await client.sismember(config.REDIS_SEEN_SET, url))  # type: ignore[no-any-return]

    async def mark_url_seen(self, url: str) -> None:
        """Add *url* to the global seen-URLs Set."""
        client = self.get_redis()
        await client.sadd(config.REDIS_SEEN_SET, url)