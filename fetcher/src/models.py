"""
SQLAlchemy ORM models for the fetcher service.

Tables
------
* ``articles``      — one row per unique article URL (upsert on re-scrape).
* ``crawl_history`` — append-only audit log; one row per scrape attempt.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Index,
    Text,
    TIMESTAMP,
    func,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base — all ORM models inherit from this."""


class Article(Base):
    __tablename__ = "articles"

    id: int = Column(BigInteger, primary_key=True, autoincrement=True)  # type: ignore[assignment]
    url: str = Column(Text, nullable=False, unique=True, index=True)  # type: ignore[assignment]
    source_domain: str = Column(Text, nullable=False, index=True)  # type: ignore[assignment]
    headline: str | None = Column(Text, nullable=True)  # type: ignore[assignment]
    content: str | None = Column(Text, nullable=True)  # type: ignore[assignment]
    content_hash: str = Column(Text, nullable=False)  # type: ignore[assignment]
    scraped_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    first_seen_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        # url uniqueness is handled by Column(unique=True) above.
        # source_domain index is handled by Column(index=True) above.
    )


class CrawlHistory(Base):
    __tablename__ = "crawl_history"

    id: int = Column(BigInteger, primary_key=True, autoincrement=True)  # type: ignore[assignment]
    article_id: int | None = Column(  # type: ignore[assignment]
        BigInteger,
        ForeignKey("articles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    url: str = Column(Text, nullable=False, index=True)  # type: ignore[assignment]
    content_hash: str = Column(Text, nullable=False)  # type: ignore[assignment]
    scraped_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        # url index is handled by Column(index=True) above.
        Index("ix_crawl_history_scraped_at", "scraped_at"),
    )
