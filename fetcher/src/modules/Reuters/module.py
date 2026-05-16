"""
Reuters site module — extends :class:`BaseModule` with Reuters-specific
headline cleanup and section extraction.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, AsyncIterator
from urllib.parse import urljoin

from botasaurus.browser import browser, Driver  # type: ignore[import-untyped]
from botasaurus.soupify import soupify  # type: ignore[import-untyped]

from ..base import BaseModule
from ..registry import register_module
from . import const
from modules.services import STEALTH_ARGUMENTS  # noqa: PLC0415

if TYPE_CHECKING:
    from bs4 import BeautifulSoup

log = logging.getLogger(__name__)


def _normalise_path(url: str) -> str:
    """Return the normalised path of *url* for comparison.

    ``/world/middle-east/`` and ``/world/middle-east?p=1`` both
    produce ``/world/middle-east/``.
    """
    from urllib.parse import urlparse
    return urlparse(url).path.rstrip("/") + "/"


# ── Reuters "load more" API base URL ───────────────────────────────
_API_BASE = (
    "https://www.reuters.com/pf/api/v3/content/fetch/"
    "articles-by-collection-alias-or-id-v1"
)

# CSS selector for the "Load more articles" button on listing pages.
_LOAD_MORE_SELECTOR = 'button[data-testid="FeedContentLoadMore"]'


@register_module(domain="reuters.com")
class ReutersModule(BaseModule):
    """Extraction logic tailored for www.reuters.com."""

    MAX_ARTICLE_LINKS: int = const.MAX_ARTICLE_LINKS

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

    # Zero-width / invisible characters that Reuters inserts into text.
    _INVISIBLE_CHARS = "\u200b\u200c\u200d\u2060\ufeff"

    @staticmethod
    def _clean_text(text: str) -> str:
        """Normalise whitespace and strip invisible Unicode characters."""
        # Collapse runs of whitespace (including &nbsp; → space).
        cleaned = " ".join(text.split())
        # Remove zero-width spaces, word-joiners, BOM, etc.
        for ch in ReutersModule._INVISIBLE_CHARS:
            cleaned = cleaned.replace(ch, "")
        return cleaned

    def _extract_content(self, soup: BeautifulSoup) -> str | None:
        """Extract article body from Reuters' paragraph-based DOM.

        Reuters (2026) wraps article text in::

            <div class="article-body-module__container__* over-*-para">
              <div class="article-body-module__content__*">
                <div data-testid="ContextWidget">  <!-- Summary bullets -->
                <div data-testid="paragraph-0">…</div>
                <div data-testid="paragraph-1">…</div>
                …
                <p data-testid="SignOff">Reporting by …</p>
                <p>Our Standards: …</p>
              </div>
            </div>

        We locate the outer container via a loose class-prefix match,
        descend into the inner content wrapper, then walk its children
        in document order collecting: Summary bullets, paragraph-N
        blocks, sign-off line, and the trust-principles badge.
        Boilerplate (promo-box, empty element divs, tags nav, toolbar)
        is skipped.
        """
        _txt = lambda el: el.get_text(separator=" ", strip=True)  # noqa: E731

        # 1 — find the outer article-body container.
        container = None
        for div in soup.find_all(
            "div",
            class_=lambda c: c and "article-body-module__container__" in c,
        ):
            container = div
            break

        if not container:
            container = soup.find("div", attrs={"data-testid": "ArticleBody"})

        if not container:
            return None

        # 2 — find the inner content wrapper.
        content_div = None
        for div in container.find_all(
            "div",
            class_=lambda c: c and "article-body-module__content__" in c,
            recursive=False,
        ):
            content_div = div
            break

        if not content_div:
            # Fallback: use the container itself (older structure).
            content_div = container

        # 3 — walk children in document order, extracting relevant parts.
        parts: list[str] = []

        for child in content_div.find_all(recursive=False):
            tid = child.get("data-testid", "")

            # ── Summary widget ──────────────────────────────
            if tid == "ContextWidget":
                summary_tab = child.find("li", attrs={"data-testid": "summary-tab"})
                if summary_tab:
                    parts.append(self._clean_text(_txt(summary_tab)))
                summary_list = child.find("ul", attrs={"data-testid": "Summary"})
                if summary_list:
                    for li in summary_list.find_all("li", recursive=False):
                        text = self._clean_text(_txt(li))
                        if text:
                            parts.append(text)
                continue

            # ── Paragraph blocks ────────────────────────────
            if tid.startswith("paragraph-"):
                text = self._clean_text(_txt(child))
                if text:
                    parts.append(text)
                continue

            # ── Sign-off ("Reporting by …") ─────────────────
            if tid == "SignOff":
                text = self._clean_text(_txt(child))
                if text:
                    parts.append(text)
                continue
            # SignOff may be nested one level deeper.
            signoff = child.find(attrs={"data-testid": "SignOff"})
            if signoff:
                text = self._clean_text(_txt(signoff))
                if text:
                    parts.append(text)
                # Keep processing: this child may also contain other items.

            # ── Trust badge ("Our Standards: …") ────────────
            if child.name == "p":
                # Remove visually-hidden accessibilty text first.
                for hidden in child.find_all(
                    "span",
                    style=lambda s: s and "clip:rect" in s,
                ):
                    hidden.decompose()
                text = self._clean_text(_txt(child))
                if text and ("Our Standards" in text or "Trust Principles" in text):
                    # Strip trailing artefacts from SVG / "opens new tab".
                    for suffix in (", opens new tab", ", opens new tab."):
                        if text.endswith(suffix):
                            text = text[: -len(suffix)].rstrip()
                    parts.append(text)
                    continue

            # ── Headings (section breaks) ───────────────────
            if child.name in ("h2", "h3", "h4"):
                text = self._clean_text(_txt(child))
                if text:
                    parts.append(text)
                continue

            # ── Explicit skips ──────────────────────────────
            if tid == "promo-box":
                continue
            if tid == "element" and not child.get_text(strip=True):
                continue
            if child.name == "nav":
                continue
            if tid == "ArticleBodyRow":
                continue

        return "\n\n".join(parts) if parts else None

    # ── listing-page detection ──────────────────────────────

    def is_listing_page(
        self, soup: BeautifulSoup, url: str, seed_prefix: str
    ) -> bool:
        """Return True if *url* is a known Reuters listing/category page.

        Checks ``const.LISTING_URL_PREFIXES`` via path-equality (not
        prefix match — articles live under the same path prefix).
        Falls back to the default link-count heuristic for unmatched
        URLs so that new sections are detected even without a
        ``const.py`` entry.
        """
        path = _normalise_path(url)
        for prefix in const.LISTING_URL_PREFIXES:
            if path == _normalise_path(prefix):
                return True
        return super().is_listing_page(soup, url, seed_prefix)

    # ── listing-page link discovery (click "Load more") ──

    async def extract_listings_links(
        self, soup: BeautifulSoup, url: str, seed_prefix: str
    ) -> AsyncIterator[str]:
        """Yield article URLs from a Reuters listing page.

        Opens a headless Chrome session, navigates to *url*, then
        clicks the "Load more articles" button repeatedly (up to
        ``const.MAX_LISTING_PAGES`` times) to expand the page.
        Finally extracts in-scope article links and yields them.
        """
        # Closure so the @browser decorator (which injects driver,
        # data as first args) can capture url/seed from outer scope.
        @browser(
            output=None,
            headless=True,
            wait_for_complete_page_load=False,
            add_arguments=STEALTH_ARGUMENTS,
        )
        def _click(driver: Driver, _data) -> list[str]:
            driver.get(url, timeout=30)
            driver.short_random_sleep()
            driver.sleep(2)

            for _ in range(const.MAX_LISTING_PAGES):
                if not driver.is_element_present(_LOAD_MORE_SELECTOR):
                    break
                try:
                    driver.click(_LOAD_MORE_SELECTOR)
                    driver.sleep(1.5)
                except Exception:
                    break

            soup = soupify(driver)
            return self.extract_links(soup, url, seed_prefix)

        try:
            links = await asyncio.to_thread(_click)
        except Exception:
            log.exception("Listing-page browser session failed for %s", url)
            return

        for link in links:
            yield link

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


@register_module(domain="www.reuters.com")
class ReutersWWWModule(ReutersModule):
    """Same extraction, registered for the www subdomain."""
    pass
