"""
Base classes and helpers for site-module authors.

Every scraper module inherits from :class:`BaseModule` and overrides
site-specific extraction logic.  The base class provides sensible defaults
that work for most news sites (meta-tag extraction, h1/headline, article
body via common CSS classes, link extraction, and page fetching).

A module's contract with the pipeline:

1. :meth:`fetch`      — how to get the page (HTTP, browser, API, …)
2. :meth:`extract`    — is this an article?  return structured data
3. :meth:`extract_links`  — what links should we crawl next?
4. :meth:`is_listing_page` — is this a category/hub page?
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from bs4 import BeautifulSoup


# ── FetchContext ───────────────────────────────────────────────────────


@dataclass
class FetchContext:
    """Metadata the pipeline passes to :meth:`BaseModule.fetch` so the
    module can make context-aware fetch decisions (e.g. skip the
    sparse-links heuristic when already at max depth).
    """

    seed_prefix: str   # root URL prefix defining crawl scope
    depth: int         # current crawl depth (0 = seed)
    max_depth: int     # configured maximum depth
    worker_id: int     # for log correlation


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
    helper, :meth:`extract_links`, or :meth:`fetch`.
    """

    # ── per-module identity ──────────────────────────────────────

    domain: str = ""  # e.g. "reuters.com" — set in subclass or by registry

    # ── fetch tunables ──────────────────────────────────────────

    # Per-class browser-required cache.  Once a fetch attempt for a
    # domain falls through to the browser, subsequent URLs for the same
    # domain skip HTTP (within the same worker process).
    _browser_required: set[str] = set()

    # Minimum number of in-scope links a page must have before we
    # trust an HTTP result.  Below this threshold we re-fetch via
    # browser (likely a JS shell that didn't render links).
    MIN_LISTING_LINKS: int = 3

    # ── article-detection tunables ──────────────────────────────

    # Minimum character count for article content.  Pages with very
    # short content (e.g. listing/category pages that happen to have an
    # <h1> and a one-line description) are treated as non-articles.
    MIN_CONTENT_LENGTH: int = 120

    # Pages with this many or more in-scope links are treated as listing
    # pages regardless of extractable content (article pages rarely link
    # to many other in-scope pages).
    MAX_ARTICLE_LINKS: int = 4

    # ── public API: fetch ────────────────────────────────────────

    def fetch(self, url: str, context: FetchContext) -> BeautifulSoup | None:
        """Fetch the page contents for *url*.

        The default implementation uses **HTTP-first with browser
        fallback**, caching the browser-required verdict per domain
        (class-level, so it persists across invocations within the
        same worker process).

        Override this method when a site needs:

        * Always-browser (skip the HTTP attempt)
        * Custom headers, cookies, or proxy
        * An API endpoint instead of HTML scraping
        * Multi-stage fetch (e.g. POST-then-GET)
        * Cached / offline responses

        Composable helpers provided so overrides can reuse pieces:

        * :meth:`_try_http_fetch`   — lightweight HTTP GET
        * :meth:`_try_browser_fetch`— headless Chrome render
        * :meth:`_has_meaningful_content` — quick soup sanity check
        * :meth:`_count_in_scope_links`   — count qualifying links
        """
        domain = urlparse(url).netloc.lower()

        # 1 — Try HTTP if we haven't cached this domain as browser-only.
        if domain not in self._browser_required:
            soup = self._try_http_fetch(url)
            if soup is not None and self._has_meaningful_content(soup):
                # Got content — but at non-terminal depths we need enough
                # links to confirm this isn't a JS shell.
                if context.depth >= context.max_depth:
                    return soup
                link_count = self._count_in_scope_links(
                    soup, url, context.seed_prefix
                )
                if link_count >= self.MIN_LISTING_LINKS:
                    return soup

        # 2 — Browser fallback (and cache the verdict).
        self._browser_required.add(domain)
        return self._try_browser_fetch(url, context.worker_id)

    # ── composable fetch helpers (use in overridden `fetch`) ─────

    def _try_http_fetch(self, url: str) -> BeautifulSoup | None:
        """Lightweight HTTP GET via botasaurus ``@request``.

        Override or call directly from a custom :meth:`fetch`.
        """
        from modules.services import http_fetch  # noqa: PLC0415
        return http_fetch(url)

    def _try_browser_fetch(self, url: str, worker_id: int) -> BeautifulSoup | None:
        """Headless Chrome render via botasaurus ``@browser``.

        Override or call directly from a custom :meth:`fetch`.
        """
        from modules.services import browser_fetch  # noqa: PLC0415
        return browser_fetch(url, worker_id)

    @staticmethod
    def _has_meaningful_content(soup) -> bool:
        """Quick heuristic: does *soup* contain at least one ``<p>`` or ``<h1>``?"""
        return bool(soup.find("p") or soup.find("h1"))

    def _count_in_scope_links(
        self, soup, url: str, seed_prefix: str
    ) -> int:
        """Return the number of in-scope links on the page.

        Uses :meth:`extract_links` internally — site-specific overrides
        are automatically respected.
        """
        return len(self.extract_links(soup, url, seed_prefix))

    # ── public API: extraction ───────────────────────────────────

    def extract(
        self,
        soup: BeautifulSoup,
        url: str,
    ) -> ArticleData | None:
        """Attempt to extract structured article data.

        Returns ``None`` if the page does not appear to be an article
        (no headline / content, content too short, or too many in-scope
        links suggesting a listing page).
        """
        headline = self._extract_headline(soup)
        content = self._extract_content(soup)

        # Must have both headline AND substantial content to be an article.
        if not content or len(content) < self.MIN_CONTENT_LENGTH:
            return None  # listing / non-article page

        return ArticleData(
            url=url,
            source_domain=self.domain,
            headline=headline,
            content=content,
        )

    def is_listing_page(self, soup: BeautifulSoup, url: str, seed_prefix: str) -> bool:
        """Return True if the page has characteristics of a listing/hub page.

        Primary signal: a high number of in-scope links.
        """
        links = self.extract_links(soup, url, seed_prefix)
        return len(links) >= self.MAX_ARTICLE_LINKS

    def extract_links(self, soup: BeautifulSoup, url: str, seed_prefix: str) -> list[str]:
        """Return all in-scope ``<a href>`` URLs whose href starts with
        *seed_prefix*.  Deduplicated within the page.  Filters out
        fragment-only URLs (``#…``), self-references, JavaScript
        placeholders (``undefined``), and other malformed URLs.
        """
        seen: set[str] = set()
        links: list[str] = []
        stripped_url = url.rstrip("/")

        for a in soup.find_all("a", href=True):
            href: str = a["href"]  # type: ignore[assignment]
            absolute = urljoin(url, href)

            # Skip fragment-only links and self-references.
            parsed = urlparse(absolute)
            clean = parsed._replace(fragment="").geturl().rstrip("/")
            if not clean or clean == stripped_url:
                continue

            # Skip JavaScript artifacts and malformed URLs.
            if "/undefined" in clean or "javascript:" in clean.lower():
                continue

            # Keep only URLs under the seed prefix.
            if clean.startswith(seed_prefix) and clean not in seen:
                seen.add(clean)
                links.append(clean)

        return links
    
    async def extract_listings_links(
        self, soup: BeautifulSoup, url: str, seed_prefix: str
    ) -> AsyncIterator[str]:
        """Yield in-scope ``<a href>`` URLs whose href starts with
        *seed_prefix*.  Deduplicated within the page.  Filters out
        fragment-only URLs (``#…``), self-references, JavaScript
        placeholders (``undefined``), and other malformed URLs.

        This is an **async generator** so that site-specific overrides
        can perform async I/O (e.g. paginated API calls, "load more"
        button interaction) while yielding links one at a time.
        """
        seen: set[str] = set()
        stripped_url = url.rstrip("/")

        for a in soup.find_all("a", href=True):
            href: str = a["href"]  # type: ignore[assignment]
            absolute = urljoin(url, href)

            # Skip fragment-only links and self-references.
            parsed = urlparse(absolute)
            clean = parsed._replace(fragment="").geturl().rstrip("/")
            if not clean or clean == stripped_url:
                continue

            # Skip JavaScript artifacts and malformed URLs.
            if "/undefined" in clean or "javascript:" in clean.lower():
                continue

            # Keep only URLs under the seed prefix.
            if clean.startswith(seed_prefix) and clean not in seen:
                seen.add(clean)
                yield clean

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
        """Try common article containers, collect ``<p>`` text.

        Falls back to ``<body>`` only as a last resort — and even then
        strips non-article sections (footer, nav, aside, author bios,
        boilerplate) to avoid extracting page chrome as content.
        """
        container = (
            soup.find("div", attrs={"data-testid": "ArticleBody"})
            or soup.find("div", class_="article-body")
            or soup.find("div", class_="article__body")
            or soup.find("div", class_="article-content")
            or soup.find("article")
            or soup.find("main")
        )

        # Last resort: <body>, but clone it and strip junk first.
        if not container:
            container = soup.find("body")
            if container:
                from copy import copy
                container = copy(container)
                # Remove structural non-article elements.
                for junk in container.find_all(["footer", "nav", "aside", "header"]):
                    junk.decompose()
                # Remove elements that match known boilerplate / bio patterns.
                for junk_sel in (
                    "[class*='author']", "[class*='Author']",
                    "[class*='byline']", "[class*='Byline']",
                    "[class*='bio']", "[class*='Bio']",
                    "[class*='footer']", "[class*='Footer']",
                    "[class*='trust']", "[class*='Trust']",
                    "[class*='standard']", "[class*='Standard']",
                    "[class*='disclaimer']", "[class*='Disclaimer']",
                    "[data-testid='Byline']",
                    "[data-testid='Attribution']",
                    "[data-testid='SiteFooter']",
                    "[data-testid='ProductCards']",
                    "[data-testid='SocialIcon']",
                ):
                    for el in container.select(junk_sel):
                        el.decompose()

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
