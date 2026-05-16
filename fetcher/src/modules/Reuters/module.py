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
