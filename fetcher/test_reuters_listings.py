"""Test Reuters ``extract_listings_links`` against a saved API fixture.

Mocks ``_try_browser_fetch`` so tests don't need a real Chrome instance.

Run from the fetcher/ directory::

    cd fetcher && uv run python test_reuters_listings.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from bs4 import BeautifulSoup  # type: ignore

from modules.Reuters.module import ReutersModule  # noqa: E402
from modules.Reuters import const  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "test_sites" / "Reuters"
API_FIXTURE = FIXTURE_DIR / "listing_api_response.json"
LISTING_URL = "https://www.reuters.com/world/middle-east/"
SEED = "https://www.reuters.com/"


def load_fixture() -> dict:
    with open(API_FIXTURE) as f:
        return json.load(f)


def _json_to_soup(data: dict) -> BeautifulSoup:
    """Wrap JSON data in minimal HTML as a browser would render it."""
    return BeautifulSoup(
        f"<html><body>{json.dumps(data)}</body></html>",
        "html.parser",
    )


async def _collect(ait):
    """Drain an async generator into a list."""
    result = []
    async for item in ait:
        result.append(item)
    return result


# ── tests ────────────────────────────────────────────────────────────

def test_is_listing_detects_known_page() -> None:
    """Known listing prefix → True."""
    mod = ReutersModule()
    mod.domain = "reuters.com"
    soup = BeautifulSoup("<html></html>", "html.parser")

    assert mod.is_listing_page(soup, LISTING_URL, SEED) is True
    # article under same section should NOT be detected as listing.
    article_url = LISTING_URL + "trump-says-isis-2026-05-16/"
    assert mod.is_listing_page(soup, article_url, SEED) is False


def test_is_listing_falls_back_to_link_count() -> None:
    """Unknown URL uses the base link-count heuristic."""
    mod = ReutersModule()
    mod.domain = "reuters.com"
    soup = BeautifulSoup("<html></html>", "html.parser")

    assert mod.is_listing_page(
        soup, "https://www.reuters.com/new-section/", SEED
    ) is False


def test_extract_listings_yields_correct_urls() -> None:
    """Mock browser returns fixture JSON; verify absolute URLs and seed filter."""
    fixture = load_fixture()
    mock_soup = _json_to_soup(fixture)
    mod = ReutersModule()
    mod.domain = "reuters.com"
    soup = BeautifulSoup("<html></html>", "html.parser")

    with patch.object(mod, "_try_browser_fetch", return_value=mock_soup):
        links = asyncio.run(
            _collect(mod.extract_listings_links(soup, LISTING_URL, SEED))
        )

    assert len(links) == 3
    for link in links:
        assert link.startswith(SEED), f"Not under seed: {link}"

    expected = [
        "https://www.reuters.com/world/middle-east/uk-sanctions-iran-linked-network-2026-05-11/",
        "https://www.reuters.com/world/americas/brazilian-flotilla-activist-returns-home-2026-05-12/",
        "https://www.reuters.com/world/middle-east/uae-has-been-secretly-carrying-out-attacks-iran-2026-05-11/",
    ]
    assert links == expected


def test_extract_listings_respects_seed_prefix() -> None:
    """Articles outside the seed_prefix are filtered out."""
    fixture = load_fixture()
    mock_soup = _json_to_soup(fixture)
    mod = ReutersModule()
    mod.domain = "reuters.com"
    soup = BeautifulSoup("<html></html>", "html.parser")

    narrow_seed = "https://www.reuters.com/world/middle-east/"
    with patch.object(mod, "_try_browser_fetch", return_value=mock_soup):
        links = asyncio.run(
            _collect(
                mod.extract_listings_links(soup, LISTING_URL, narrow_seed)
            )
        )

    assert len(links) == 2
    for link in links:
        assert link.startswith(narrow_seed)


def test_extract_listings_paginates() -> None:
    """A full batch triggers a second browser call to check for more."""
    articles = []
    for i in range(const.LISTING_PAGE_SIZE):
        articles.append({
            "id": f"ID{i}",
            "canonical_url": f"/world/middle-east/article-{i}/",
            "website": "reuters",
            "title": f"Article {i}",
        })
    full_page = {"result": {"articles": articles}}
    empty_page = {"result": {"articles": []}}

    mod = ReutersModule()
    mod.domain = "reuters.com"
    soup = BeautifulSoup("<html></html>", "html.parser")

    call_count = 0

    def side_effect(_url, worker_id=0):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _json_to_soup(full_page)
        return _json_to_soup(empty_page)

    with patch.object(mod, "_try_browser_fetch", side_effect=side_effect):
        links = asyncio.run(
            _collect(mod.extract_listings_links(soup, LISTING_URL, SEED))
        )

    assert len(links) == const.LISTING_PAGE_SIZE
    assert call_count == 2


def test_extract_listings_empty_response() -> None:
    """Empty articles list → no links yielded."""
    mod = ReutersModule()
    mod.domain = "reuters.com"
    soup = BeautifulSoup("<html></html>", "html.parser")
    mock_soup = _json_to_soup({"result": {"articles": []}})

    with patch.object(mod, "_try_browser_fetch", return_value=mock_soup):
        links = asyncio.run(
            _collect(mod.extract_listings_links(soup, LISTING_URL, SEED))
        )

    assert len(links) == 0


def test_extract_listings_missing_alias() -> None:
    """URL that can't resolve to an alias → nothing yielded, no crash."""
    mod = ReutersModule()
    mod.domain = "reuters.com"
    soup = BeautifulSoup("<html></html>", "html.parser")

    links = asyncio.run(
        _collect(
            mod.extract_listings_links(
                soup, "https://www.reuters.com/", SEED
            )
        )
    )
    assert len(links) == 0


def test_extract_listings_browser_fails_gracefully() -> None:
    """Browser returns None → no links, no crash."""
    mod = ReutersModule()
    mod.domain = "reuters.com"
    soup = BeautifulSoup("<html></html>", "html.parser")

    with patch.object(mod, "_try_browser_fetch", return_value=None):
        links = asyncio.run(
            _collect(mod.extract_listings_links(soup, LISTING_URL, SEED))
        )

    assert len(links) == 0


def test_extract_listings_bad_json_graceful() -> None:
    """Bogus text in browser → no links, no crash."""
    mod = ReutersModule()
    mod.domain = "reuters.com"
    soup = BeautifulSoup("<html></html>", "html.parser")
    bad_soup = BeautifulSoup("<html><body>not json</body></html>", "html.parser")

    with patch.object(mod, "_try_browser_fetch", return_value=bad_soup):
        links = asyncio.run(
            _collect(mod.extract_listings_links(soup, LISTING_URL, SEED))
        )

    assert len(links) == 0


if __name__ == "__main__":
    tests = [
        ("is_listing_detects_known_page", test_is_listing_detects_known_page),
        ("is_listing_falls_back", test_is_listing_falls_back_to_link_count),
        ("yields_correct_urls", test_extract_listings_yields_correct_urls),
        ("respects_seed_prefix", test_extract_listings_respects_seed_prefix),
        ("paginates", test_extract_listings_paginates),
        ("empty_response", test_extract_listings_empty_response),
        ("missing_alias", test_extract_listings_missing_alias),
        ("browser_fails", test_extract_listings_browser_fails_gracefully),
        ("bad_json", test_extract_listings_bad_json_graceful),
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
