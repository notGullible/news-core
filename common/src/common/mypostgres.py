"""
Asynchronous PostgreSQL connection manager.

Mirrors the pattern in myredis.py: a shared async engine + session factory,
with init/close lifecycle methods and a per-call session context manager.

Usage::

    db = MyPostgres()
    if not await db.init_db():
        log.critical("Postgres unreachable")
    ...
    async with db.get_session() as session:
        ...
    await db.close_db()

In DEBUG mode, tables are created automatically on init.
In production mode, Alembic migrations are expected to have been run
(e.g. ``uv run alembic upgrade head`` prior to starting the service).
"""

from __future__ import annotations

import logging
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import text

from common.config import (
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_DB,
    POSTGRES_USER,
    POSTGRES_PASSWORD,
    POSTGRES_POOL_MIN,
    POSTGRES_POOL_MAX,
    DEBUG,
)

log = logging.getLogger(__name__)

# Build the asyncpg connection URL.
_DATABASE_URL: str = (
    f"postgresql+asyncpg://"
    f"{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}"
    f"/{POSTGRES_DB}"
)


class MyPostgres:
    """Thin wrapper around a SQLAlchemy async engine + sessionmaker."""

    def __init__(self) -> None:
        self._engine = create_async_engine(
            _DATABASE_URL,
            pool_size=POSTGRES_POOL_MIN,
            max_overflow=POSTGRES_POOL_MAX - POSTGRES_POOL_MIN,
            echo=False,
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    # ── session management ─────────────────────────────────

    def get_session(self) -> AsyncSession:
        """Return a new AsyncSession. Use as an async context manager.

        .. code-block:: python

            async with db.get_session() as session:
                session.add(...)
                await session.commit()
        """
        return self._session_factory()

    # ── lifecycle ──────────────────────────────────────────

    async def init_db(self) -> bool:
        """Verify the database is reachable and optionally create tables.

        Returns True on success, False if the connection fails.
        """
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

            if DEBUG:
                # Auto-create all ORM tables in development.
                from common.models import Base  # noqa: PLC0415

                async with self._engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                log.info("DEBUG mode: created/verified all database tables")

            return True

        except Exception:
            log.exception("Could not connect to PostgreSQL")
            return False

    async def close_db(self) -> None:
        """Gracefully dispose of the engine pool."""
        try:
            await self._engine.dispose()
            log.info("Closed PostgreSQL connection pool")
        except Exception:
            log.warning("Could not close PostgreSQL connection pool")
