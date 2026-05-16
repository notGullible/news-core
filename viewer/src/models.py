"""SQLAlchemy ORM models — mirrors the fetcher's ``articles`` table (read-only)."""

from sqlalchemy import BigInteger, Column, Index, Text, TIMESTAMP, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Article(Base):
    __tablename__ = "articles"

    id: int = Column(BigInteger, primary_key=True, autoincrement=True)  # type: ignore[assignment]
    url: str = Column(Text, nullable=False, unique=True)  # type: ignore[assignment]
    source_domain: str = Column(Text, nullable=False)  # type: ignore[assignment]
    headline: str | None = Column(Text, nullable=True)  # type: ignore[assignment]
    content: str | None = Column(Text, nullable=True)  # type: ignore[assignment]
    content_hash: str = Column(Text, nullable=False)  # type: ignore[assignment]
    scraped_at = Column(TIMESTAMP(timezone=True), nullable=False)
    first_seen_at = Column(TIMESTAMP(timezone=True), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("url", name="uq_articles_url"),
        Index("ix_articles_source_domain", "source_domain"),
    )
