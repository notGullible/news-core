from botasaurus.browser import browser, Driver  # type: ignore
from botasaurus.soupify import soupify  # type: ignore

# Prevent the "Running…" banner from printing on every call.
import botasaurus.decorators_common  # type: ignore

from ..services import Scrapper

botasaurus.decorators_common.first_run = True


class ReutersScrapper(Scrapper):
    """Browser-based scrapper for Reuters article pages.

    Uses a real Chrome browser via ``@browser`` because Reuters serves
    its content exclusively via JavaScript rendering.  Extracts:

    * headline
    * date_published
    * author
    * content (full article body)
    * section (category)
    * url (canonical)
    """

    def scrap(self):
        if not self.link:
            return None

        @browser(
            output=None,
            headless=True,  # TODO: make configurable via config.py or env
            wait_for_complete_page_load=True,
        )  # type: ignore
        def _scrap(driver: Driver, _data):  # type: ignore
            self.log.info(f"Navigating to {self.link}")
            driver.get(self.link)  # type: ignore

            # Brief pause to let dynamic content settle.
            driver.short_random_sleep()  # type: ignore

            soup = soupify(driver)  # type: ignore
            current_url = driver.current_url  # type: ignore

            result: dict = {
                "url": current_url or self.link,
                "headline": _extract_headline(soup),
                "date_published": _extract_date(soup),
                "author": _extract_author(soup),
                "section": _extract_section(soup),
                "content": _extract_content(soup),
            }

            # Post-condition: if we still have no headline and no content the
            # page structure may have changed.
            if not result["headline"] and not result["content"]:
                result["error"] = (
                    "No article content extracted — "
                    "the Reuters page structure may have changed."
                )

            return result

        return _scrap()  # type: ignore


# ------------------------------------------------------------------ helpers


def _extract_headline(soup):
    """Return the article headline (or None)."""
    # Reuters typically uses a prominent <h1> with data-testid.
    for selector in (
        soup.find("h1", attrs={"data-testid": "Heading"}),
        soup.find("h1"),
        soup.find("meta", property="og:title"),
        soup.find("title"),
    ):
        if selector:
            text = (
                selector.get("content")
                if selector.name == "meta"
                else selector.get_text()
            )
            if text:
                # Strip common Reuters suffix.
                text = text.strip()
                for suffix in (" | Reuters", " - Reuters"):
                    if text.endswith(suffix):
                        text = text[: -len(suffix)]
                return text
    return None


def _extract_date(soup):
    """Return the publication date string (or None)."""
    # 1 — meta tags (most reliable).
    for meta in soup.find_all("meta"):
        if meta.get("property") == "article:published_time" or meta.get("name") in (
            "article:published_time",
            "pubdate",
            "date",
            "parsely-pub-date",
        ):
            return meta.get("content")

    # 2 — <time> element.
    time_el = soup.find("time")
    if time_el:
        return time_el.get("datetime") or time_el.get_text(strip=True)

    # 3 — common Reuters date containers.
    for cls in ("article__date", "ArticleHeader_date", "date"):
        el = soup.find(class_=cls)
        if el:
            return el.get_text(strip=True)

    return None


def _extract_author(soup):
    """Return the author name(s) as a string (or None)."""
    # 1 — meta tags.
    for meta in soup.find_all("meta"):
        if meta.get("name") in ("author", "article:author", "sailthru.author"):
            return meta.get("content")
        if meta.get("property") == "article:author":
            return meta.get("content")

    # 2 — common Reuters byline classes.
    for sel in (
        "a[data-testid='Byline']",
        ".article__byline",
        ".byline",
        ".article-byline",
    ):
        el = soup.select_one(sel)
        if el:
            text = el.get_text(strip=True)
            # Strip "By " prefix if present.
            if text.lower().startswith("by "):
                text = text[3:]
            return text

    return None


def _extract_section(soup):
    """Return the article section / category (or None)."""
    for meta in soup.find_all("meta"):
        if meta.get("property") == "article:section" or meta.get("name") in (
            "article:section",
            "section",
        ):
            return meta.get("content")

    # Breadcrumbs / section links.
    for sel in (
        "a[data-testid='Section']",
        ".article__section",
        ".breadcrumb a:last-child",
    ):
        el = soup.select_one(sel)
        if el:
            return el.get_text(strip=True)

    return None


def _extract_content(soup):
    """Return the full article body text (or None)."""
    # Try known Reuters article containers first.
    container = (
        soup.find("article")
        or soup.find("div", class_="article-body")
        or soup.find("div", class_="article__body")
        or soup.find("div", class_="article-content")
        or soup.find("div", attrs={"data-testid": "Article"})
    )

    # Fallback: use <main> or <body>.
    if not container:
        container = soup.find("main") or soup.find("body")

    if not container:
        return None

    # Collect text from <p> elements (skip empty / whitespace-only).
    paragraphs = [
        p.get_text(strip=True)
        for p in container.find_all("p")
        if p.get_text(strip=True)
    ]

    if not paragraphs:
        return None

    return "\n\n".join(paragraphs)
