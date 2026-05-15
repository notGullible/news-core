"""
Base classes and helpers for site-module authors.

Every scraper module inherits from :class:`BaseModule` and overrides
site-specific extraction logic.  The base class provides sensible defaults
that work for most news sites (meta-tag extraction, h1/headline, article
body via common CSS classes).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from urllib.parse import urljoin
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bs4 import BeautifulSoup


# ── ArticleData ────────────────────────────────────────────────────────


@dataclass
class ArticleData:
    """Structured article payload ready for PostgreSQL storage."""

    url: str
    source_domain: str
    headline: str | None
    content: str | None
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        raw = (self.headline or "") + (self.content or "")
        self.content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── BaseModule ─────────────────────────────────────────────────────────


class BaseModule:
    """Default extraction logic suitable for most news / content sites.

    Subclass, set ``domain``, and optionally override any ``_extract_*``
    helper or :meth:`extract_links`.
    """

    # ── per-module identity ──────────────────────────────────────

    domain: str = ""  # e.g. "reuters.com" — set in subclass or by registry

    # ── public API ────────────────────────────────────────────────

    def extract(
        self,
        soup: BeautifulSoup,
        url: str,
    ) -> ArticleData | None:
        """Attempt to extract structured article data.

        Returns ``None`` if the page does not appear to be an article
        (no headline and no content found).  This ``None`` is used by
        the pipeline as the signal that the page is a *listing* page.
        """
        headline = self._extract_headline(soup)
        content = self._extract_content(soup)

        if not headline and not content:
            return None  # listing / non-article page

        return ArticleData(
            url=url,
            source_domain=self.domain,
            headline=headline,
            content=content,
        )

    def extract_links(self, soup: BeautifulSoup, url: str, seed_prefix: str) -> list[str]:
        """Return all in-scope ``<a href>`` URLs whose href starts with
        *seed_prefix*.  Deduplicated within the page.
        """
        seen: set[str] = set()
        links: list[str] = []

        for a in soup.find_all("a", href=True):
            href: str = a["href"]  # type: ignore[assignment]
            absolute = urljoin(url, href)
            # Keep only URLs under the seed prefix and within the same domain.
            if absolute.startswith(seed_prefix) and absolute not in seen:
                seen.add(absolute)
                links.append(absolute)

        return links

    # ── shared extraction helpers (override in subclass) ──────────

    def _extract_headline(self, soup: BeautifulSoup) -> str | None:
        """Try, in order: ``og:title`` → ``<h1>`` → ``<title>``."""
        for selector in (
            soup.find("meta", property="og:title"),
            soup.find("h1"),
            soup.find("title"),
        ):
            if selector:
                text = (
                    selector.get("content")
                    if selector.name == "meta"
                    else selector.get_text()
                )
                if text:
                    return text.strip()
        return None

    def _extract_content(self, soup: BeautifulSoup) -> str | None:
        """Try common article containers, collect ``<p>`` text."""
        container = (
            soup.find("article")
            or soup.find(class_="article-body")
            or soup.find(class_="article__body")
            or soup.find(class_="article-content")
            or soup.find("main")
            or soup.find("body")
        )
        if not container:
            return None

        paragraphs = [
            p.get_text(strip=True)
            for p in container.find_all("p")
            if p.get_text(strip=True)
        ]
        return "\n\n".join(paragraphs) if paragraphs else None

    def _extract_date(self, soup: BeautifulSoup) -> str | None:
        """Try ``article:published_time`` meta → ``pubdate`` → ``<time>``."""
        for meta in soup.find_all("meta"):
            prop = meta.get("property", "")
            name = meta.get("name", "")
            if prop == "article:published_time" or name in (
                "article:published_time",
                "pubdate",
                "date",
                "parsely-pub-date",
            ):
                return meta.get("content")

        time_el = soup.find("time")
        if time_el:
            return time_el.get("datetime") or time_el.get_text(strip=True)
        return None

    def _extract_author(self, soup: BeautifulSoup) -> str | None:
        """Try author-related meta tags → common byline CSS classes."""
        for meta in soup.find_all("meta"):
            name = meta.get("name", "")
            prop = meta.get("property", "")
            if name in ("author", "article:author", "sailthru.author") or prop == "article:author":
                return meta.get("content")

        for cls in ("article__author", "byline", "author-name", "ArticleHeader_author"):
            el = soup.find(class_=cls)
            if el:
                return el.get_text(strip=True)
        return None

    def _extract_section(self, soup: BeautifulSoup) -> str | None:
        """Try ``article:section`` meta → breadcrumbs → section links."""
        for meta in soup.find_all("meta"):
            prop = meta.get("property", "")
            name = meta.get("name", "")
            if prop == "article:section" or name in ("article:section", "section"):
                return meta.get("content")

        for sel in (
            "a[data-testid='Section']",
            ".article__section",
            ".breadcrumb a:last-child",
        ):
            el = soup.select_one(sel)
            if el:
                return el.get_text(strip=True)
        return None
