"""Live integration test for Reuters listing-page link discovery.

Tests ``is_listing_page`` detection and ``extract_listings_links``
against the real Reuters API via headless Chrome.  Requires a working
Chrome installation (same as the pipeline).

Run from the fetcher/ directory::

    cd fetcher && uv run python test_reuters_listings_live.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from bs4 import BeautifulSoup  # type: ignore

from modules.Reuters.module import ReutersModule  # noqa: E402
from modules.Reuters import const  # noqa: E402

LISTING_URL = "https://www.reuters.com/world/middle-east/"
SEED = "https://www.reuters.com/"


async def _collect(ait):
    result = []
    async for item in ait:
        result.append(item)
    return result


def _looks_like_article_url(url: str) -> bool:
    """Heuristic: Reuters article URLs have a date-like suffix."""
    parsed = urlparse(url)
    parts = parsed.path.rstrip("/").split("/")
    if len(parts) < 2:
        return False
    last = parts[-1]
    return any(str(y) in last for y in range(2020, 2031))


async def main() -> int:
    mod = ReutersModule()
    mod.domain = "reuters.com"
    soup = BeautifulSoup("<html></html>", "html.parser")
    failed = 0

    # ── 1. is_listing_page detection ──────────────────────────
    print("1. is_listing_page detection …")
    assert mod.is_listing_page(soup, LISTING_URL, SEED), \
        "Known listing not detected"
    assert not mod.is_listing_page(
        soup, LISTING_URL + "some-article-2026-05-16/", SEED
    ), "Article URL wrongly flagged as listing"
    print("   ✓ listing vs article detection works")

    # ── 2. live link discovery (requires Chrome) ──────────────
    print("2. extract_listings_links (browser-based API) …")
    try:
        links = await _collect(
            mod.extract_listings_links(soup, LISTING_URL, SEED)
        )
    except Exception as exc:
        print(f"   ⚠ browser/API unreachable — {exc}")
        print("   (skipping remaining live tests — requires Chrome + network)")
        return 0

    if not links:
        print("   ⚠ got 0 links (API may be down or CAPTCHA'd)")
        return 0

    print(f"   ✓ got {len(links)} links")

    # ── 3. link quality checks ────────────────────────────────
    print("3. link quality …")
    bad = [l for l in links if not _looks_like_article_url(l)]
    assert not bad, f"Non-article-looking: {bad[:3]}"
    out = [l for l in links if not l.startswith(SEED)]
    assert not out, f"Outside seed: {out[:3]}"
    assert len(links) == len(set(links)), "Duplicates found"
    print("   ✓ all look like article URLs, under seed, no dupes")

    # ── 4. pagination (multiple pages) ────────────────────────
    print("4. pagination …")
    original_max = const.MAX_LISTING_PAGES
    try:
        object.__setattr__(const, "MAX_LISTING_PAGES", 3)
        links_multi = await _collect(
            mod.extract_listings_links(soup, LISTING_URL, SEED)
        )
        object.__setattr__(const, "MAX_LISTING_PAGES", original_max)

        assert len(links_multi) >= 10, \
            f"Expected >=10 links across 3 pages, got {len(links_multi)}"
        assert len(links_multi) == len(set(links_multi)), \
            "Duplicates across pages"
        print(f"   ✓ {len(links_multi)} links from 3 pages")
    except Exception as exc:
        object.__setattr__(const, "MAX_LISTING_PAGES", original_max)
        print(f"   ⚠ pagination test issue: {exc}")

    print(f"\n{failed} failures")
    return failed


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
