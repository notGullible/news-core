"""Test Reuters ``extract_listings_links`` — mock the browser via asyncio.to_thread.

Mocks ``asyncio.to_thread`` so tests don't need a real Chrome instance.

Run from the fetcher/ directory::

    cd fetcher && uv run python test_reuters_listings.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from bs4 import BeautifulSoup  # type: ignore

from modules.Reuters.module import ReutersModule  # noqa: E402

LISTING_URL = "https://www.reuters.com/world/middle-east/"
SEED = "https://www.reuters.com/"


async def _collect(ait):
    result = []
    async for item in ait:
        result.append(item)
    return result


# ── tests ────────────────────────────────────────────────────────────

def test_is_listing_detects_known_page() -> None:
    mod = ReutersModule()
    mod.domain = "reuters.com"
    soup = BeautifulSoup("<html></html>", "html.parser")
    assert mod.is_listing_page(soup, LISTING_URL, SEED) is True
    article_url = LISTING_URL + "trump-says-isis-2026-05-16/"
    assert mod.is_listing_page(soup, article_url, SEED) is False


def test_is_listing_falls_back_to_link_count() -> None:
    mod = ReutersModule()
    mod.domain = "reuters.com"
    soup = BeautifulSoup("<html></html>", "html.parser")
    assert not mod.is_listing_page(
        soup, "https://www.reuters.com/new-section/", SEED
    )


def test_extract_listings_yields_correct_urls() -> None:
    expected = [
        "https://www.reuters.com/world/middle-east/article-one-2026-05-11/",
        "https://www.reuters.com/world/middle-east/article-two-2026-05-12/",
        "https://www.reuters.com/world/americas/article-three-2026-05-12/",
    ]
    mod = ReutersModule()
    mod.domain = "reuters.com"
    soup = BeautifulSoup("<html></html>", "html.parser")

    with patch("asyncio.to_thread", return_value=expected):
        links = asyncio.run(
            _collect(mod.extract_listings_links(soup, LISTING_URL, SEED))
        )
    assert links == expected


def test_extract_listings_empty_response() -> None:
    mod = ReutersModule()
    mod.domain = "reuters.com"
    soup = BeautifulSoup("<html></html>", "html.parser")

    with patch("asyncio.to_thread", return_value=[]):
        links = asyncio.run(
            _collect(mod.extract_listings_links(soup, LISTING_URL, SEED))
        )
    assert len(links) == 0


def test_extract_listings_browser_fails() -> None:
    mod = ReutersModule()
    mod.domain = "reuters.com"
    soup = BeautifulSoup("<html></html>", "html.parser")

    with patch("asyncio.to_thread", side_effect=RuntimeError("Browser crashed")):
        links = asyncio.run(
            _collect(mod.extract_listings_links(soup, LISTING_URL, SEED))
        )
    assert len(links) == 0


def test_extract_listings_many_pages() -> None:
    mod = ReutersModule()
    mod.domain = "reuters.com"
    soup = BeautifulSoup("<html></html>", "html.parser")
    many = [
        f"https://www.reuters.com/world/middle-east/art-{i}/"
        for i in range(50)
    ]

    with patch("asyncio.to_thread", return_value=many):
        links = asyncio.run(
            _collect(mod.extract_listings_links(soup, LISTING_URL, SEED))
        )
    assert len(links) == 50
    assert len(set(links)) == 50


if __name__ == "__main__":
    tests = [
        ("is_listing_detects_known_page", test_is_listing_detects_known_page),
        ("is_listing_falls_back", test_is_listing_falls_back_to_link_count),
        ("yields_correct_urls", test_extract_listings_yields_correct_urls),
        ("empty_response", test_extract_listings_empty_response),
        ("browser_fails", test_extract_listings_browser_fails),
        ("many_pages", test_extract_listings_many_pages),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  ✗ {name}  — {exc}")
        except Exception:
            failed += 1
            print(f"  ✗ {name}  — UNEXPECTED ERROR")
            import traceback
            traceback.print_exc()
    print(f"\n{failed} failed, {len(tests) - failed} passed")
    sys.exit(1 if failed else 0)
