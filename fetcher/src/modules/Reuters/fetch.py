from botasaurus.request import request  # type: ignore
from botasaurus.soupify import soupify  # type: ignore

# Prevent the "Running…" banner from printing on every call.
import botasaurus.decorators_common  # type: ignore

from ..services import Fetcher

botasaurus.decorators_common.first_run = True


class ReutersFetcher(Fetcher):
    """Lightweight request-based fetcher for Reuters URLs.

    DEPRECATED for Reuters article pages — Reuters requires JavaScript
    and will not serve content to plain HTTP requests.
    Kept as a fallback / reference for other (non-JS) news sources.
    Use :class:`ReutersScrapper` for actual Reuters scraping.
    """

    def fetch(self):
        if not self.link:
            return None

        @request(output=None)  # type: ignore
        def _fetch(req, _data):  # type: ignore
            response = req.get(self.link)  # type: ignore
            soup = soupify(response)  # type: ignore

            # Try to extract article content (works only for non-JS pages).
            result: dict = {
                "url": self.link,
                "headline": _extract_headline(soup),
                "date_published": _extract_date(soup),
                "author": _extract_author(soup),
                "content": _extract_content(soup),
            }

            # Detect if the site returned a JS-only shell.
            if not result["headline"] and not result["content"]:
                result["error"] = (
                    "No content extracted — site likely requires JavaScript. "
                    "Use ReutersScrapper instead."
                )

            return result

        return _fetch()  # type: ignore


# ------------------------------------------------------------------ helpers

def _extract_headline(soup):
    """Return the article headline (or None)."""
    for selector in (
        soup.find("meta", property="og:title"),
        soup.find("h1"),
        soup.find("title"),
    ):
        if selector:
            text = selector.get("content") if selector.name == "meta" else selector.get_text()
            if text:
                return text.strip()
    return None


def _extract_date(soup):
    """Return the publication date string (or None)."""
    for meta in soup.find_all("meta"):
        if meta.get("property") == "article:published_time" or meta.get("name") in (
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


def _extract_author(soup):
    """Return the author name(s) as a string (or None)."""
    for meta in soup.find_all("meta"):
        if meta.get("name") in ("author", "article:author", "sailthru.author"):
            return meta.get("content")
        if meta.get("property") == "article:author":
            return meta.get("content")

    for cls in ("article__author", "byline", "author-name", "ArticleHeader_author"):
        el = soup.find(class_=cls)
        if el:
            return el.get_text(strip=True)
    return None


def _extract_content(soup):
    """Return the article body text as a single string (or None)."""
    container = (
        soup.find("article")
        or soup.find(class_="article-body")
        or soup.find(class_="article__body")
        or soup.find(class_="body-content")
        or soup.find("main")
        or soup.find("body")
    )
    if not container:
        return None

    paragraphs = [p.get_text(strip=True) for p in container.find_all("p")]
    if not paragraphs:
        return None
    return "\n\n".join(paragraphs)