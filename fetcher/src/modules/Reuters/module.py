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

    # ── content (Reuters-specific: paragraphs via data-testid) ─

    def _extract_content(self, soup: BeautifulSoup) -> str | None:
        """Extract article body from Reuters' paragraph-based DOM.

        Reuters (2026) wraps article text in::

            <div class="article-body-module__container__* over-*-para">
              <div data-testid="paragraph-0">…</div>
              <div data-testid="paragraph-1">…</div>
              …
            </div>

        We locate the container via a loose class-prefix match, then
        collect every ``data-testid^="paragraph-"`` element inside it.
        Boilerplate blocks (newsletter sign-up, reporting credits,
        trust principles, advertisement) are skipped.
        """
        # 1 — find the article-body container via class prefix match.
        container = None
        for div in soup.find_all(
            "div",
            class_=lambda c: c and "article-body-module__container__" in c,
        ):
            container = div
            break

        if not container:
            # Fallback: try older Reuters structure.
            container = soup.find("div", attrs={"data-testid": "ArticleBody"})

        if not container:
            return None

        # 2 — collect paragraph-N blocks AND h2 headings in DOM order.
        parts: list[str] = []
        skip_phrases = (
            "advertisement",
            "scroll to continue",
            "sign up",
            "our standards",
            "trust principles",
            "reporting by",
            "additional reporting",
            "writing by",
            "editing by",
            "thomson reuters",
        )

        # Walk immediate children of the container in document order.
        direct_children = list(container.find_all(recursive=False))
        if not direct_children:
            # Container might have a wrapper; try numbered paragraph-N fallback.
            i = 0
            while True:
                para = container.find("div", attrs={"data-testid": f"paragraph-{i}"})
                if para is None:
                    break
                text = para.get_text(strip=True)
                if text and not any(skip in text.lower() for skip in skip_phrases):
                    parts.append(text)
                i += 1
        else:
            for child in direct_children:
                tid = child.get("data-testid", "")
                if tid.startswith("paragraph-"):
                    text = child.get_text(strip=True)
                    if text and not any(skip in text.lower() for skip in skip_phrases):
                        parts.append(text)
                elif child.name == "h2":
                    text = child.get_text(strip=True)
                    if text:
                        parts.append(text)
                elif child.name in ("h3", "h4"):
                    text = child.get_text(strip=True)
                    if text:
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
