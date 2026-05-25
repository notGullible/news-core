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

import config

log = logging.getLogger(__name__)

# Build the asyncpg connection URL.
_DATABASE_URL: str = (
    f"postgresql+asyncpg://"
    f"{config.POSTGRES_USER}:{config.POSTGRES_PASSWORD}"
    f"@{config.POSTGRES_HOST}:{config.POSTGRES_PORT}"
    f"/{config.POSTGRES_DB}"
)


class MyPostgres:
    """Thin wrapper around a SQLAlchemy async engine + sessionmaker."""

    def __init__(self) -> None:
        self._engine = create_async_engine(
            _DATABASE_URL,
            pool_size=config.POSTGRES_POOL_MIN,
            max_overflow=config.POSTGRES_POOL_MAX - config.POSTGRES_POOL_MIN,
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

            if config.DEBUG:
                # Auto-create all ORM tables in development.
                from models import Base  # noqa: PLC0415  — deferred import

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
