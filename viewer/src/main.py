"""NG Viewer — browse articles scraped by the fetcher pipeline."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

app = FastAPI(
    title="NG Viewer",
    description="Browse articles collected by the NG fetcher pipeline.",
    version="0.1.0",
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


def render(name: str, request: Request, **context) -> HTMLResponse:
    """Render a Jinja2 template with *request* and optional *context*."""
    ctx = {"request": request, **context}
    html = jinja_env.get_template(name).render(ctx)
    return HTMLResponse(html)


STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

from routers.articles import router as articles_router  # noqa: E402

app.include_router(articles_router)


@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/articles", status_code=302)


@app.get("/health")
async def health():
    from sqlalchemy import text
    from database import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as exc:
        return {"status": "error", "database": str(exc)}
