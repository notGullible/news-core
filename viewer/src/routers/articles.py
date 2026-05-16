"""Article listing and detail routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from database import get_session
from main import render

router = APIRouter(prefix="/articles", tags=["articles"])


async def _get_stats(session: AsyncSession) -> dict:
    """Return total article count and distinct domain count."""
    from models import Article  # noqa: PLC0415
    total = (await session.execute(select(func.count(Article.id)))).scalar() or 0
    domains = (
        await session.execute(
            select(func.count(func.distinct(Article.source_domain)))
        )
    ).scalar() or 0
    return {"total": total, "domains": domains}


# ── listing ──────────────────────────────────────────────────────────


@router.get("")
@router.get("/")
async def list_articles(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1),
    search: str = Query("", max_length=200),
    domain: str = Query(""),
):
    page_size = config.PAGE_SIZE
    offset = (page - 1) * page_size

    from models import Article  # noqa: PLC0415

    # Base query
    base = select(Article)
    count_base = select(func.count(Article.id))

    # Filters
    if search:
        pattern = f"%{search}%"
        base = base.where(
            (Article.headline.ilike(pattern)) | (Article.content.ilike(pattern))
        )
        count_base = count_base.where(
            (Article.headline.ilike(pattern)) | (Article.content.ilike(pattern))
        )
    if domain:
        base = base.where(Article.source_domain == domain)
        count_base = count_base.where(Article.source_domain == domain)

    # Total count
    total = (await session.execute(count_base)).scalar() or 0

    # Paginated results
    rows = (
        await session.execute(
            base.order_by(Article.scraped_at.desc())
            .offset(offset)
            .limit(page_size)
        )
    ).scalars().all()

    total_pages = max(1, (total + page_size - 1) // page_size)

    # All known domains (for filter dropdown).
    domain_rows = (
        await session.execute(
            select(Article.source_domain, func.count(Article.id))
            .group_by(Article.source_domain)
            .order_by(Article.source_domain)
        )
    ).all()
    domains: list[tuple[str, int]] = [(str(r[0]), int(r[1])) for r in domain_rows]

    # Quick stats.
    stats = await _get_stats(session)

    return render(
        "index.html",
        request,
        articles=rows,
        page=page,
        total_pages=total_pages,
        total=total,
        search=search,
        domain=domain,
        domains=domains,
        stats=stats,
    )


# ── detail ───────────────────────────────────────────────────────────


@router.get("/{article_id:int}")
async def article_detail(
    request: Request,
    article_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    from models import Article  # noqa: PLC0415

    result = await session.execute(
        select(Article).where(Article.id == article_id)
    )
    article = result.scalar_one_or_none()

    if not article:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/articles", status_code=303)

    stats = await _get_stats(session)

    return render("article.html", request, article=article, stats=stats)
