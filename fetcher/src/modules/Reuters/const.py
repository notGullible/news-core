"""
Reuters listing-page configuration.

Maps known listing/category page URL prefixes to their API
collection aliases.  Used by :class:`ReutersModule` for
``is_listing_page`` detection and API-based link discovery.
"""

from __future__ import annotations

from urllib.parse import urlparse


# ── Listing-page detection ──────────────────────────────────────────

# URL prefixes that identify a Reuters listing / category page.
# ``is_listing_page`` checks these first; if the URL doesn't match
# any prefix, it falls through to the default link-count heuristic.
LISTING_URL_PREFIXES: list[str] = [
    "https://www.reuters.com/world/middle-east/",
    "https://www.reuters.com/world/africa/",
    "https://www.reuters.com/world/americas/",
    "https://www.reuters.com/world/asia-pacific/",
    "https://www.reuters.com/world/china/",
    "https://www.reuters.com/world/europe/",
    "https://www.reuters.com/world/india/",
    "https://www.reuters.com/world/iran/",
    "https://www.reuters.com/world/japan/",
    "https://www.reuters.com/world/uk/",
    "https://www.reuters.com/world/us/",
    "https://www.reuters.com/business/",
    "https://www.reuters.com/business/energy/",
    "https://www.reuters.com/business/finance/",
    "https://www.reuters.com/technology/",
    "https://www.reuters.com/sports/",
    "https://www.reuters.com/science/",
    "https://www.reuters.com/lifestyle/",
    "https://www.reuters.com/legal/",
    "https://www.reuters.com/sustainability/",
]


# ── API collection-alias resolution ────────────────────────────────

# Override the automatic alias derivation for URL paths that
# don't produce the correct ``collection_alias``.
# Key = same URL prefix format as LISTING_URL_PREFIXES.
COLLECTION_ALIAS_OVERRIDES: dict[str, str] = {
    # e.g. "https://www.reuters.com/world/": "world-home",
}


# ── API pagination tunables ─────────────────────────────────────────

LISTING_PAGE_SIZE: int = 10   # articles returned per API call
MAX_LISTING_PAGES: int = 1000    # safety cap — 5 × 10 = 50 articles max

# Threshold for the link-count fallback in ``is_listing_page``.
# Reuters article pages carry 4–15 related-story sidebar links;
# listing/category pages have 50+.  This value cleanly separates them.
MAX_ARTICLE_LINKS: int = 10


# ── Alias derivation ────────────────────────────────────────────────

def url_to_alias(url: str) -> str:
    """Derive the Reuters API ``collection_alias`` from a listing URL.

    Strips the domain, query string, and fragment, then replaces
    path separators with hyphens::

        https://www.reuters.com/world/middle-east/?foo#bar
        → "world-middle-east"
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        return ""
    return path.replace("/", "-")


def resolve_alias(url: str) -> str | None:
    """Return the ``collection_alias`` for *url*, or ``None``.

    Checks ``COLLECTION_ALIAS_OVERRIDES`` first (path-equality on
    the URL path), then derives from the URL path.
    """
    path = urlparse(url).path.rstrip("/") + "/"
    for prefix, alias in COLLECTION_ALIAS_OVERRIDES.items():
        if urlparse(prefix).path.rstrip("/") + "/" == path:
            return alias
    alias = url_to_alias(url)
    return alias if alias else None
