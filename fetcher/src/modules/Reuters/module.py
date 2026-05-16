"""
Reuters site module — extends :class:`BaseModule` with Reuters-specific
headline cleanup and section extraction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import BaseModule
from ..registry import register_module

if TYPE_CHECKING:
    from bs4 import BeautifulSoup


@register_module(domain="reuters.com")
class ReutersModule(BaseModule):
    """Extraction logic tailored for www.reuters.com."""

    # ── headline ────────────────────────────────────────────────

    def _extract_headline(self, soup: BeautifulSoup) -> str | None:
        text = super()._extract_headline(soup)
        if text:
            # Strip common Reuters suffixes from <title> / og:title.
            for suffix in (" | Reuters", " - Reuters"):
                if text.endswith(suffix):
                    text = text[: -len(suffix)]
        return text

    # ── content (Reuters-specific: paragraphs are <div>, not <p>) ─

    def _extract_content(self, soup: BeautifulSoup) -> str | None:
        """Extract article body from Reuters' ``<div>``-based paragraphs.

        Reuters uses ``<div class="article-body-module__paragraph__*">``
        for article text and ``<h2>`` for section headings.  Boilerplate
        (newsletter, ads, reporting credits, trust badge) is in ``<p>``
        or ``<div class="article-body-module__element__*">`` elements
        which we explicitly skip.
        """
        container = soup.find("div", attrs={"data-testid": "ArticleBody"})
        if not container:
            return None

        parts: list[str] = []
        for el in container.select(
            "div[class*='article-body-module__paragraph__'], "
            "h2[class*='article-body-module__heading__']"
        ):
            text = el.get_text(strip=True)
            if text and not any(
                skip in text.lower()
                for skip in ("advertisement", "sign up", "our standards", "trust principles")
            ):
                parts.append(text)

        return "\n\n".join(parts) if parts else None

    # ── section ─────────────────────────────────────────────────

    def _extract_section(self, soup: BeautifulSoup) -> str | None:
        # 1 — meta tags.
        for meta in soup.find_all("meta"):
            prop = meta.get("property", "")
            name = meta.get("name", "")
            if prop == "article:section" or name in ("article:section", "section"):
                return meta.get("content")

        # 2 — Reuters breadcrumb / section link.
        for sel in (
            "a[data-testid='Section']",
            ".article__section",
            ".breadcrumb a:last-child",
        ):
            el = soup.select_one(sel)
            if el:
                return el.get_text(strip=True)

        return None

    # ── author (Reuters-specific byline) ─────────────────────────

    def _extract_author(self, soup: BeautifulSoup) -> str | None:
        # Try parent implementation first (meta tags, common classes).
        result = super()._extract_author(soup)
        if result:
            return result

        # Reuters-specific byline selectors.
        for sel in (
            "a[data-testid='Byline']",
            ".article__byline",
            ".byline",
            ".article-byline",
        ):
            el = soup.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if text.lower().startswith("by "):
                    text = text[3:]
                return text

        return None
