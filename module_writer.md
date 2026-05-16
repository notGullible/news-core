# Module Writer's Guide

> **Audience:** AI coding agents (or humans) tasked with adding a new site module or modifying an existing one.
>
> **Prerequisite knowledge:** You should already understand the high-level architecture (see `plan.md`). This document zooms in on *modules specifically* — how they work, how to write one, and how the surrounding infrastructure calls them.

---

## Table of Contents

1. [What Is a Module?](#1-what-is-a-module)
2. [Module Lifecycle (How the Pipeline Calls You)](#2-module-lifecycle)
3. [Quick-Start: Add a New Site](#3-quick-start-add-a-new-site)
4. [The BaseModule API — Reference](#4-the-basemodule-api--reference)
5. [Override Recipes](#5-override-recipes)
   5a. [Extraction Recipes](#5a-extraction-recipes)
   5b. [Fetch Recipes](#5b-fetch-recipes)
6. [The ArticleData Return Value](#6-the-articledata-return-value)
7. [How Article Detection Works (Non-Trivial!)](#7-how-article-detection-works-non-trivial)
8. [How Link Extraction Works](#8-how-link-extraction-works)
9. [The Fetch API — HTTP, Browser, Custom](#9-the-fetch-api--http-browser-custom)
10. [Registration System](#10-registration-system)
11. [Testing Your Module](#11-testing-your-module)
12. [Real-World Example: The Reuters Module (Annotated)](#12-real-world-example-the-reuters-module-annotated)
13. [Common Pitfalls](#13-common-pitfalls)
14. [File Checklist for a New Module](#14-file-checklist-for-a-new-module)

---

## 1. What Is a Module?

A **module** is a single Python class that knows how to **fetch pages**, **extract article data**, and **find in-scope links** for a specific website. It lives in `fetcher/src/modules/<SiteName>/module.py` and inherits from `BaseModule`.

The contract:

| Method | Purpose | Returns |
|--------|---------|---------|
| `fetch(url, context)` | Get the page (HTTP, browser, API, …) | `BeautifulSoup` **or `None`** |
| `extract(soup, url)` | Pull article fields from the page | `ArticleData` **or `None`** |
| `extract_links(soup, url, seed_prefix)` | Find navigable links under seed prefix | `list[str]` |
| `is_listing_page(soup, url, seed_prefix)` | Is this a category/listing/hub? | `bool` |

**The module does NOT:**
- Connect to databases (the `ModuleManager` does that)
- Enqueue links into Redis (the `ModuleManager` handles dedup + enqueue)

The module **can** control how pages are fetched by overriding `fetch()`. The base class provides a sensible HTTP-first + browser-fallback default that works for most sites.

---

## 2. Module Lifecycle (How the Pipeline Calls You)

Here's exactly what happens when a worker picks up a URL belonging to your module:

```
Worker dequeues Redis message {"site": "https://yoursite.com/article/123", "depth": 1, "seed": "https://yoursite.com/"}

    │
    ▼
ModuleManager.process(task)
    │
    ├── 1. Parses domain from URL → "yoursite.com"
    │
    ├── 2. get_module("yoursite.com") → YourModule (from registry)
    │       Falls back to BaseModule if no module registered
    │
    ├── 3. Builds a FetchContext and calls module.fetch(url, context)
    │       └─ Default: HTTP-first → if sparse/empty → browser fallback
    │          Custom: module may use browser-only, API, cached data, …
    │       └─ Returns BeautifulSoup or None
    │
    ├── 4. Calls module.is_listing_page(soup, url, seed)
    │       If True → skip extraction, go straight to link discovery
    │       If False → proceed to extraction
    │
    ├── 5. Calls module.extract(soup, url)
    │       ├─ Returns ArticleData → ModuleManager upserts into PostgreSQL
    │       │   (articles table: upsert on URL conflict)
    │       │   (crawl_history table: always append)
    │       └─ Returns None → page is not an article, skip storage
    │
    ├── 6. IF depth < max_depth:
    │       Calls module.extract_links(soup, url, seed)
    │       └─ Returns list[str] of in-scope URLs
    │          ModuleManager deduplicates (Redis Set + Postgres) and re-enqueues
    │
    └── 7. Logs stats, sleeps a politeness delay, loops
```

**Key insight:** Steps 4 and 5 happen for *every* page, even non-articles. This is by design — the module is the sole authority on what constitutes an article.

---

## 3. Quick-Start: Add a New Site

### Step A — Create the module file

Create `fetcher/src/modules/BBC/module.py`:

```python
""" BBC News site module. """

from __future__ import annotations

from ..base import BaseModule
from ..registry import register_module


@register_module(domain="bbc.com")       # ⬅ must match the netloc
class BBCModule(BaseModule):
    """Extraction logic for www.bbc.com and www.bbc.co.uk."""

    # The base class defaults work for ~80% of news sites.
    # Only override what BBC does differently.
    pass
```

That's it. **If BBC uses standard `<article>` / `<h1>` / `<meta property="og:title">` patterns, this module already works — including fetching.**

### Step B — Register the module

Open `fetcher/src/modules/__init__.py` and add one import line:

```python
"""Package root — explicit imports so each ``@register_module`` decorator fires."""

from .Reuters.module import ReutersModule  # noqa: F401
from .BBC.module import BBCModule          # noqa: F401   ⬅ ADD THIS

__all__ = ["ReutersModule", "BBCModule"]
```

### Step C — Add a seed URL (optional)

If you want to start crawling BBC, add a seed to `fetcher/src/sites.py`:

```python
qualified_news = [
    "https://www.reuters.com/",
    "https://www.bbc.com/news/",       # ⬅ ADD THIS
    # ...
]
```

Or push directly to Redis:
```python
await myredis.enqueue_stream("fetcher", {"site": "https://www.bbc.com/news/"})
```

### Step D — Restart the workers

The module is auto-discovered on import. Just restart `main.py`.

---

## 4. The BaseModule API — Reference

### 4a. Fetch API

| Method | Purpose | Signature |
|--------|---------|-----------|
| `fetch(url, context)` | Get the page contents | `(str, FetchContext) → BeautifulSoup \| None` |
| `_try_http_fetch(url)` | Lightweight HTTP GET (composable helper) | `(str) → BeautifulSoup \| None` |
| `_try_browser_fetch(url, worker_id)` | Headless Chrome render (composable helper) | `(str, int) → BeautifulSoup \| None` |
| `_has_meaningful_content(soup)` | Check if soup has at least one `<p>` or `<h1>` (composable helper) | `(soup) → bool` |
| `_count_in_scope_links(soup, url, seed_prefix)` | Count in-scope links (uses `extract_links` internally) | `(soup, str, str) → int` |

### 4b. FetchContext

```python
@dataclass
class FetchContext:
    seed_prefix: str   # root URL prefix defining crawl scope
    depth: int         # current crawl depth (0 = seed)
    max_depth: int     # configured maximum depth
    worker_id: int     # for log correlation
```

### 4c. Fetch tunables (class-level)

```python
class BaseModule:
    _browser_required: set[str] = set()   # domains that need browser (per-class cache)
    MIN_LISTING_LINKS: int = 3            # min in-scope links to trust HTTP result
```

### 4d. Extraction API

| Method | Purpose | Signature |
|--------|---------|-----------|
| `extract(soup, url)` | Try to pull article data; returns `None` if not an article | `(BeautifulSoup, str) → ArticleData \| None` |
| `is_listing_page(soup, url, seed_prefix)` | Is this a listing/hub page? | `(BeautifulSoup, str, str) → bool` |
| `extract_links(soup, url, seed_prefix)` | Find in-scope `<a href>` URLs | `(BeautifulSoup, str, str) → list[str]` |

### 4e. Extraction tunables (class-level)

```python
class BaseModule:
    domain: str = ""                    # Set by @register_module decorator
    MIN_CONTENT_LENGTH: int = 120       # Pages with less content → not articles
    MAX_ARTICLE_LINKS: int = 4          # Pages with ≥ this many in-scope links → listing
```

### 4f. Extraction helpers (override for site-specific CSS selectors)

| Helper | What it does | Returns |
|--------|-------------|---------|
| `_extract_headline(soup)` | Finds the article title | `str \| None` |
| `_extract_content(soup)` | Collects all `<p>` text from the article container | `str \| None` |
| `_extract_date(soup)` | Finds the publication date | `str \| None` |
| `_extract_author(soup)` | Finds the author name(s) | `str \| None` |
| `_extract_section(soup)` | Finds the category/section | `str \| None` |

**Note:** The current `ArticleData` only carries `headline`, `content`, and `content_hash` into the database. The `_extract_date`, `_extract_author`, and `_extract_section` helpers exist for future use (e.g., a `metadata JSONB` column). Override them anyway — they cost nothing and your module is future-proof.

### 4g. The default `fetch()` implementation

```python
def fetch(self, url: str, context: FetchContext) -> BeautifulSoup | None:
    domain = urlparse(url).netloc.lower()

    # 1 — Try HTTP if we haven't cached this domain as browser-only.
    if domain not in self._browser_required:
        soup = self._try_http_fetch(url)
        if soup is not None and self._has_meaningful_content(soup):
            if context.depth >= context.max_depth:
                return soup
            link_count = self._count_in_scope_links(soup, url, context.seed_prefix)
            if link_count >= self.MIN_LISTING_LINKS:
                return soup

    # 2 — Browser fallback (and cache the verdict).
    self._browser_required.add(domain)
    return self._try_browser_fetch(url, context.worker_id)
```

### 4h. The default `extract()` implementation

```python
def extract(self, soup, url):
    headline = self._extract_headline(soup)    # calls your override
    content = self._extract_content(soup)      # calls your override

    if not content or len(content) < self.MIN_CONTENT_LENGTH:
        return None   # not an article

    return ArticleData(
        url=url,
        source_domain=self.domain,
        headline=headline,    # note: None is OK here
        content=content,
    )
```

### 4i. The default `is_listing_page()` implementation

```python
def is_listing_page(self, soup, url, seed_prefix):
    links = self.extract_links(soup, url, seed_prefix)
    return len(links) >= self.MAX_ARTICLE_LINKS
```

### 4j. The default `extract_links()` implementation

```python
def extract_links(self, soup, url, seed_prefix):
    seen = set()
    links = []
    stripped_url = url.rstrip("/")

    for a in soup.find_all("a", href=True):
        absolute = urljoin(url, a["href"])

        # Filter out: fragments, self-references, javascript:, /undefined
        parsed = urlparse(absolute)
        clean = parsed._replace(fragment="").geturl().rstrip("/")
        if not clean or clean == stripped_url:
            continue
        if "/undefined" in clean or "javascript:" in clean.lower():
            continue

        if clean.startswith(seed_prefix) and clean not in seen:
            seen.add(clean)
            links.append(clean)

    return links
```

---

## 5. Override Recipes

### 5a. Extraction Recipes

#### Recipe A: Headline has a site-specific suffix

```python
def _extract_headline(self, soup):
    text = super()._extract_headline(soup)   # use default logic first
    if text:
        for suffix in (" | BBC News", " - BBC News"):
            if text.endswith(suffix):
                text = text[:-len(suffix)]
    return text
```

#### Recipe B: Content uses a non-standard container

```python
def _extract_content(self, soup):
    # This site puts article text in <div class="story-body__inner">
    container = soup.find("div", class_="story-body__inner")
    if not container:
        return None

    paragraphs = [
        p.get_text(strip=True)
        for p in container.find_all("p")
        if p.get_text(strip=True)
    ]
    return "\n\n".join(paragraphs) if paragraphs else None
```

#### Recipe C: Content uses numbered paragraph blocks (like Reuters 2026)

Reuters wraps article text in an outer container with a nested
content wrapper.  You must **descend into the content wrapper** —
walking the outer container's direct children only finds structural
chrome (images, toolbars), not the paragraphs themselves.

```python
def _extract_content(self, soup):
    # 1 — outer container (class-prefix match for resilience).
    container = None
    for div in soup.find_all(
        "div",
        class_=lambda c: c and "article-body-module__container__" in c,
    ):
        container = div
        break
    if not container:
        return None

    # 2 — descend into the inner content wrapper.
    content = None
    for div in container.find_all(
        "div",
        class_=lambda c: c and "article-body-module__content__" in c,
        recursive=False,
    ):
        content = div
        break
    if not content:
        content = container  # fallback

    # 3 — walk direct children of the content wrapper.
    parts = []
    for child in content.find_all(recursive=False):
        tid = child.get("data-testid", "")

        # Paragraph blocks (data-testid="paragraph-0", "paragraph-1", …)
        if tid.startswith("paragraph-"):
            # Use separator=" " so inline <a> tags don't swallow spaces.
            text = child.get_text(separator=" ", strip=True)
            if text:
                parts.append(text)

        # Headings embedded between paragraphs.
        elif child.name in ("h2", "h3", "h4"):
            text = child.get_text(separator=" ", strip=True)
            if text:
                parts.append(text)

    return "\n\n".join(parts) if parts else None
```

**Key point:** `get_text(separator=" ", strip=True)` — the `separator=" "`
is critical when paragraphs contain inline `<a>` tags.  Without it,
`"President <a>Donald Trump</a> said"` becomes `"PresidentDonald Trumpsaid"`.

#### Recipe D: Increase the minimum content threshold

```python
class ShortNewsModule(BaseModule):
    MIN_CONTENT_LENGTH = 50   # default is 120

class LongFormOnly(BaseModule):
    MIN_CONTENT_LENGTH = 500
```

#### Recipe E: Change the listing-page detection threshold

```python
class HighLinkArticles(BaseModule):
    MAX_ARTICLE_LINKS = 15   # default is 4.  Up to 14 in-scope links is still an article
```

#### Recipe F: Extract links from non-`<a>` tags

```python
def extract_links(self, soup, url, seed_prefix):
    links = super().extract_links(soup, url, seed_prefix)
    for el in soup.select("[data-url]"):
        href = urljoin(url, el["data-url"])
        if href.startswith(seed_prefix):
            links.append(href)
    return list(set(links))
```

#### Recipe G: Exclude certain URL patterns from links

```python
def extract_links(self, soup, url, seed_prefix):
    links = super().extract_links(soup, url, seed_prefix)
    return [
        l for l in links
        if not any(x in l for x in ("/author/", "/tag/", "/video/", "/live/"))
    ]
```

#### Recipe H: Add an exclusion filter for non-article content

```python
def _extract_content(self, soup):
    container = soup.find("div", class_="article-body")
    if not container:
        return None

    for junk_sel in ("[class*='newsletter']", "[class*='related-stories']"):
        for el in container.select(junk_sel):
            el.decompose()

    paragraphs = [
        p.get_text(separator=" ", strip=True)
        for p in container.find_all("p")
        if p.get_text(strip=True)
    ]
    return "\n\n".join(paragraphs) if paragraphs else None
```

#### Recipe I: Clean invisible Unicode characters

Many sites insert zero-width spaces (``U+200B``), word joiners
(``U+2060``), and other invisible formatting characters into text.
Strip them after extraction so downstream consumers get clean text:

```python
_INVISIBLE_CHARS = "\u200b\u200c\u200d\u2060\ufeff"

@staticmethod
def _clean_text(text: str) -> str:
    # Collapse runs of whitespace (including non-breaking spaces).
    cleaned = " ".join(text.split())
    # Remove zero-width spaces, word-joiners, BOM, etc.
    for ch in _INVISIBLE_CHARS:
        cleaned = cleaned.replace(ch, "")
    return cleaned
```

Call it on every extracted text fragment:
```python
text = self._clean_text(child.get_text(separator=" ", strip=True))
```

#### Recipe J: Decompose hidden accessibility spans before extracting

Some sites embed visually-hidden text inside links (e.g. ``", opens
new tab"`` for screen readers).  Decompose these **before** calling
``get_text()`` so they don't pollute your content:

```python
# Remove visually-hidden spans (clip:rect is the tell-tale style).
for hidden in element.find_all(
    "span",
    style=lambda s: s and "clip:rect" in s,
):
    hidden.decompose()

text = element.get_text(separator=" ", strip=True)
```

### 5b. Fetch Recipes

#### Recipe I: Always use browser (skip HTTP attempt)

```python
class PureBrowserModule(BaseModule):
    """This site blocks all HTTP requests — go straight to Chrome."""

    def fetch(self, url, context):
        return self._try_browser_fetch(url, context.worker_id)
```

#### Recipe J: Always use HTTP (never use browser)

```python
class PureHTTPModule(BaseModule):
    """This site serves all content via static HTML — never pay the browser tax."""

    def fetch(self, url, context):
        return self._try_http_fetch(url)
```

#### Recipe K: Custom API-based fetch

```python
import requests

class APIModule(BaseModule):
    """This site has a JSON API — use it instead of scraping HTML."""

    def fetch(self, url, context):
        article_id = url.rstrip("/").split("/")[-1]
        resp = requests.get(f"https://api.example.com/article/{article_id}")
        if resp.status_code != 200:
            return None
        data = resp.json()
        html = f"<html><body><h1>{data['title']}</h1><p>{data['body']}</p></body></html>"
        return BeautifulSoup(html, "html.parser")
```

#### Recipe L: Browser-first but with fallback to HTTP

```python
class BrowserFirstModule(BaseModule):
    """This site's HTTP responses are unreliable — try browser first."""

    def fetch(self, url, context):
        soup = self._try_browser_fetch(url, context.worker_id)
        if soup is not None:
            return soup
        # Fallback to HTTP if browser fails (e.g., no Chrome binary)
        return self._try_http_fetch(url)
```

#### Recipe M: Add custom browser arguments (stealth)

```python
class StealthModule(BaseModule):
    """Add extra anti-detection flags to the Chrome launch."""

    def _try_browser_fetch(self, url, worker_id):
        # Re-implement with custom arguments.
        # (Note: this requires importing browser_fetch internals.)
        from modules.services import browser_fetch  # noqa: PLC0415
        return browser_fetch(url, worker_id)
```

#### Recipe N: HTTP with custom session (cookies, headers)

```python
from botasaurus.request import request  # type: ignore
from botasaurus.soupify import soupify  # type: ignore

class SessionModule(BaseModule):
    """Requests that need a login session."""

    def _try_http_fetch(self, url):
        try:
            @request(output=None)  # type: ignore
            def _fetch(req, _data):
                # Set cookies / headers before the request.
                response = req.get(url)
                return soupify(response)
            return _fetch()
        except Exception:
            return None
```

---

## 6. The ArticleData Return Value

`ArticleData` is a `@dataclass` defined in `modules/base.py`:

```python
@dataclass
class ArticleData:
    url: str                              # canonical URL of the page
    source_domain: str                    # e.g. "reuters.com"
    headline: str | None                  # may be None
    content: str | None                   # may be None
    content_hash: str = field(init=False) # auto-computed SHA-256
```

The hash is computed automatically in `__post_init__`:
```python
raw = (self.headline or "") + (self.content or "")
self.content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

**You never construct `content_hash` yourself.** Just pass `headline` and `content` — the dataclass does the rest.

**Important glitch fix:** Always pass `content=` as a keyword argument (not positional). The `field(init=False)` on `content_hash` can cause positional-argument misalignment. This only affects you if your module overrides `extract()`. The `BaseModule.extract()` already uses keyword arguments.

---

## 7. How Article Detection Works (Non-Trivial!)

The pipeline uses **three independent signals** to decide whether a page is an article:

### Signal 1: Content extraction (`extract()` returns `ArticleData` or `None`)

`BaseModule.extract()` returns `None` when `content` is shorter than `MIN_CONTENT_LENGTH` (default 120 characters).

**Important distinction:** `_extract_content()` returns `None` vs `extract()` returns `None`.
- `_extract_content()` returns `None` → no article container found at all
- `extract()` returns `None` → content too short to be an article **or** no content found

### Signal 2: Link-count heuristic (`is_listing_page()`)

Returns `True` when the page has ≥ `MAX_ARTICLE_LINKS` (default 4) in-scope links. **This runs BEFORE extraction.** If True, extraction is skipped entirely.

### Signal 3: Module override

Override `is_listing_page()` for site-specific logic (CSS class checks, URL structure).

### Decision flow in ModuleManager

```
For each URL:
  │
  ├── is_listing_page(soup, url, seed) ?
  │     ├── True  → skip extract(), go to extract_links()
  │     └── False → extract()
  │                   ├── ArticleData → store in Postgres
  │                   └── None → skip storage
  │
  └── IF depth < max_depth: extract_links()
```

---

## 8. How Link Extraction Works

`extract_links()` returns URLs that are **candidates** for crawling. The ModuleManager then:

1. **Redis Set dedup** — `SISMEMBER fetcher:seen_urls` (O(1))
2. **Postgres dedup** — `SELECT id FROM articles WHERE url = ?` (catches URLs from previous runs)
3. **Enqueues to Redis stream** — `XADD fetcher * site <url> depth <depth+1> seed <seed> ...`

### Default link extraction filters

| Filtered out | Example | Reason |
|-------------|---------|--------|
| Fragment-only URLs | `#comments` | Same page |
| Self-references | Link to current URL | No-op |
| JavaScript placeholders | `javascript:void(0)` | Not navigable |
| Undefined artifacts | `/undefined` | JS-rendered shell |
| URLs outside seed prefix | `https://other.com/page` | Out of scope |

---

## 9. The Fetch API — HTTP, Browser, Custom

### How the default `fetch()` works

`BaseModule.fetch()` implements **HTTP-first with browser fallback**:

```
BaseModule.fetch(url, context):
    │
    ├── 1. HTTP try (_try_http_fetch)
    │       ├── No soup OR no meaningful content → fall through
    │       └── Has content & (at max depth OR has enough links) → return soup
    │
    └── 2. Browser fallback (_try_browser_fetch)
            └── Cache "domain needs browser" → skips HTTP next time
```

### The composable helpers

These are `BaseModule` methods you can call from an overridden `fetch()`:

| Helper | What it does |
|--------|-------------|
| `_try_http_fetch(url)` | Calls `services.http_fetch()` — botasaurus `@request` |
| `_try_browser_fetch(url, worker_id)` | Calls `services.browser_fetch()` — botasaurus `@browser` with stealth args |
| `_has_meaningful_content(soup)` | `bool(soup.find("p") or soup.find("h1"))` |
| `_count_in_scope_links(soup, url, seed_prefix)` | `len(self.extract_links(soup, url, seed_prefix))` |

### When to override `fetch()`

- **Always-browser site** — HTTP always returns empty/blocked → override to skip HTTP
- **Always-HTTP site** — no JS → never pay the Chrome launch cost
- **API-based site** — has a JSON/GraphQL API for content → bypass HTML entirely
- **Custom auth** — needs cookies, headers, or multi-stage login
- **Cached/offline** — use pre-saved HTML files for testing

### Browser verdict caching

`_browser_required` is a **class-level `set[str]`**. Once a domain is added (meaning HTTP was tried and failed), all subsequent calls for that domain within the same worker process skip the HTTP attempt. Each worker process has its own cache (modules are imported fresh per subprocess).

### Relationship to `services.py`

`services.py` is the low-level transport. Site modules should **not** import it directly — use the `_try_*` helper methods on `BaseModule` instead. Those helpers internally import from `services.py` with deferred imports to avoid circular dependencies.

---

## 10. Registration System

### The `@register_module` decorator

```python
_registry: dict[str, Type[BaseModule]] = {}

def register_module(domain: str):
    def decorator(cls):
        cls.domain = domain
        _registry[domain] = cls
        return cls
    return decorator
```

### How modules are discovered

Explicit imports in `modules/__init__.py`:

```python
from .Reuters.module import ReutersModule  # noqa: F401
from .BBC.module import BBCModule          # noqa: F401
```

When `modules/__init__.py` is imported, these fire, decorators run, registry populates.

### Fallback behavior

```python
module_cls = get_module(domain) or BaseModule
module = module_cls()
module.domain = domain
```

Unregistered domains get `BaseModule` with generic extraction. Works surprisingly well.

---

## 11. Testing Your Module

### Test extraction against a saved HTML file

```python
from bs4 import BeautifulSoup
from modules.BBC.module import BBCModule

with open("bbc_article.html") as f:
    soup = BeautifulSoup(f.read(), "html.parser")

mod = BBCModule()
result = mod.extract(soup, "https://www.bbc.com/news/world-12345")
print("Headline:", result.headline)
print("Content length:", len(result.content) if result.content else 0)
```

### Test fetch logic with a mock FetchContext

```python
from modules.base import FetchContext

ctx = FetchContext(seed_prefix="https://www.bbc.com/", depth=0, max_depth=3, worker_id=0)

# Test that fetch() exists and accepts the right signature
import inspect
sig = inspect.signature(mod.fetch)
print("fetch signature:", sig)
```

### Test against a listing page

```python
with open("bbc_category.html") as f:
    soup = BeautifulSoup(f.read(), "html.parser")

result = mod.extract(soup, "https://www.bbc.com/news/")
print(result)  # should be None
print(mod.is_listing_page(soup, "https://www.bbc.com/news/", "https://www.bbc.com/"))  # True
```

### Test the registration

```python
from modules.registry import get_module
assert get_module("bbc.com") is not None
```

### ⚠️  Test fixture must match what the pipeline processes

If a site requires browser rendering (JS-heavy, like Reuters), your
saved HTML fixture **must be saved after browser rendering**.  An
HTTP-only snapshot is a JS shell with no article text — your module
will pass tests against that shell but fail on real pages.

Capture a fixture by either:
* Saving the DOM from the browser after the page loads, or
* Running the pipeline once, copying the rendered HTML from the
  ``soup`` that ``fetch()`` returns.

### 🔍 Debugging: tests pass but pipeline produces different output

This almost always means the **module isn't being used at runtime**.
The pipeline silently falls back to ``BaseModule`` when domain
registration is missing (see Pitfall #2 below).

Quick check — add a temporary print to ``module_manager.process()``:

```python
module_cls = get_module(domain) or BaseModule
print(f"[{self.worker_id}] {domain!r} → {module_cls.__name__}")
```

If you see ``BaseModule`` for a domain you registered, check:
1. Did you register **both** ``example.com`` and ``www.example.com``?
2. Is your ``@register_module`` decorator actually firing?  (Add a
   ``print`` inside the decorator body temporarily.)
3. Did you add the import line in ``modules/__init__.py``?

### Test with multi-domain

```python
@register_module(domain="bbc.com")
class BBCModule(BaseModule):
    pass

@register_module(domain="bbc.co.uk")
class BBCUKModule(BBCModule):   # extends the same module!
    pass
```

---

## 12. Real-World Example: The Reuters Module (Annotated)

```python
"""Reuters site module."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import BaseModule
from ..registry import register_module

if TYPE_CHECKING:
    from bs4 import BeautifulSoup


@register_module(domain="reuters.com")
class ReutersModule(BaseModule):
    """Extraction logic tailored for www.reuters.com.

    Does NOT override fetch() — the default HTTP-first + browser-fallback
    works correctly for Reuters (HTTP returns empty, browser renders JS).

    Registers TWO domains (reuters.com AND www.reuters.com) because
    ``urlparse(netloc)`` returns ``www.reuters.com`` for all article
    URLs.  Without the www variant the pipeline silently falls back
    to BaseModule — see Pitfall #2.
    """

    # ── headline ────────────────────────────────────────────────

    def _extract_headline(self, soup: BeautifulSoup) -> str | None:
        text = super()._extract_headline(soup)
        if text:
            for suffix in (" | Reuters", " - Reuters"):
                if text.endswith(suffix):
                    text = text[: -len(suffix)]
        return text

    # ── content (Reuters 2026: numbered paragraphs + Summary) ───

    # Zero-width / invisible characters Reuters inserts into text.
    _INVISIBLE_CHARS = "\u200b\u200c\u200d\u2060\ufeff"

    @staticmethod
    def _clean_text(text: str) -> str:
        """Normalise whitespace and strip invisible Unicode characters."""
        cleaned = " ".join(text.split())
        for ch in ReutersModule._INVISIBLE_CHARS:
            cleaned = cleaned.replace(ch, "")
        return cleaned

    def _extract_content(self, soup: BeautifulSoup) -> str | None:
        _txt = lambda el: el.get_text(separator=" ", strip=True)

        # 1 — outer container (class-prefix match for resilience).
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

        # 2 — descend into the inner content wrapper.
        content_div = None
        for div in container.find_all(
            "div",
            class_=lambda c: c and "article-body-module__content__" in c,
            recursive=False,
        ):
            content_div = div
            break
        if not content_div:
            content_div = container  # fallback for older structure

        # 3 — walk children in document order.
        parts: list[str] = []
        for child in content_div.find_all(recursive=False):
            tid = child.get("data-testid", "")

            # Summary widget (bullet points).
            if tid == "ContextWidget":
                tab = child.find("li", attrs={"data-testid": "summary-tab"})
                if tab:
                    parts.append(self._clean_text(_txt(tab)))
                ul = child.find("ul", attrs={"data-testid": "Summary"})
                if ul:
                    for li in ul.find_all("li", recursive=False):
                        t = self._clean_text(_txt(li))
                        if t:
                            parts.append(t)
                continue

            # Numbered paragraph blocks.
            if tid.startswith("paragraph-"):
                t = self._clean_text(_txt(child))
                if t:
                    parts.append(t)
                continue

            # Sign-off line ("Reporting by …").
            if tid == "SignOff":
                t = self._clean_text(_txt(child))
                if t:
                    parts.append(t)
                continue
            signoff = child.find(attrs={"data-testid": "SignOff"})
            if signoff:
                t = self._clean_text(_txt(signoff))
                if t:
                    parts.append(t)

            # Trust badge ("Our Standards: …").
            if child.name == "p":
                # Decompose visually-hidden spans first.
                for hidden in child.find_all(
                    "span", style=lambda s: s and "clip:rect" in s
                ):
                    hidden.decompose()
                t = self._clean_text(_txt(child))
                if t and "Our Standards" in t:
                    for sfx in (", opens new tab", ", opens new tab."):
                        if t.endswith(sfx):
                            t = t[: -len(sfx)].rstrip()
                    parts.append(t)
                    continue

            # Headings.
            if child.name in ("h2", "h3", "h4"):
                t = self._clean_text(_txt(child))
                if t:
                    parts.append(t)
                continue

            # Explicit skips (promo-box, empty element divs, nav, toolbar).
            if tid in ("promo-box",):
                continue
            if tid == "element" and not child.get_text(strip=True):
                continue
            if child.name == "nav" or tid == "ArticleBodyRow":
                continue

        return "\n\n".join(parts) if parts else None

    # ── section ─────────────────────────────────────────────────

    def _extract_section(self, soup: BeautifulSoup) -> str | None:
        for meta in soup.find_all("meta"):
            prop = meta.get("property", "")
            name = meta.get("name", "")
            if prop == "article:section" or name in ("article:section", "section"):
                return meta.get("content")
        for sel in ("a[data-testid='Section']", ".article__section"):
            el = soup.select_one(sel)
            if el:
                return el.get_text(strip=True)
        return None

    # ── author ──────────────────────────────────────────────────

    def _extract_author(self, soup: BeautifulSoup) -> str | None:
        result = super()._extract_author(soup)
        if result:
            return result
        for sel in ("a[data-testid='Byline']", ".article__byline"):
            el = soup.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if text.lower().startswith("by "):
                    text = text[3:]
                return text
        return None


@register_module(domain="www.reuters.com")
class ReutersWWWModule(ReutersModule):
    """Same extraction, registered for the www subdomain.

    ``urlparse('https://www.reuters.com/…').netloc`` returns
    ``'www.reuters.com'``, not ``'reuters.com'``.  Without this
    second registration the pipeline silently falls back to
    BaseModule — see Pitfall #2.
    """
    pass
```

### What ReutersModule does NOT override

- `fetch()` — default HTTP-first + browser-fallback works
- `extract()` — default logic (check `MIN_CONTENT_LENGTH`) is fine
- `is_listing_page()` — link-count heuristic works
- `extract_links()` — standard `<a href>` extraction works
- `_extract_date()` — meta-tag approach works

### Design decisions worth highlighting

* **Two registrations:** Always check ``urlparse(url).netloc`` for
  the actual domain your site uses.  Reuters articles live at
  ``www.reuters.com``.
* **Descend into content wrapper:** The outer
  ``article-body-module__container__*`` div holds structural chrome
  (images, toolbars).  Real content is one level deeper in
  ``article-body-module__content__*``.  Walking the wrong level
  silently produces empty content.
* **``get_text(separator=" ", strip=True)``:** Paragraphs contain
  inline ``<a>`` tags ("President ``<a>``Donald Trump``</a>`` said").
  Without ``separator=" "`` the space around the link disappears.
* **``_clean_text``:** Strips invisible characters (zero-width
  spaces, word joiners) that Reuters inserts.  Call it on every
  extracted fragment.
* **Decompose hidden spans:** The trust badge's ``<a>`` contains a
  ``<span style="clip:rect(0 0 0 0)">, opens new tab</span>`` for
  screen readers.  Decompose it before ``get_text()``.
* **Include, don't skip:** The old module filtered out "Reporting
  by…" and "Our Standards…" as boilerplate.  We now include
  everything that is part of the article — the downstream consumer
  decides what to display.

**Lesson:** Only override what's broken. Start with an empty subclass.

---

## 13. Common Pitfalls

### ❌ Pitfall 1: Forgetting to register

```python
class BBCModule(BaseModule):   # ← no @register_module decorator!
    pass
```

**Symptom:** BBC URLs fall through to `BaseModule`. No errors — just silently works less well.

### ❌ Pitfall 2: Wrong domain string — silent fallback to BaseModule

`urlparse("https://www.bbc.com/news").netloc` → `"www.bbc.com"`. Register BOTH:

```python
@register_module(domain="www.bbc.com")
class BBCModule(BaseModule):
    pass

@register_module(domain="bbc.com")
class BBCBareModule(BBCModule):
    pass
```

**This is the #1 cause of "tests pass but pipeline produces garbage."**
The module_manager catches the mismatch with ``get_module(domain) or
BaseModule`` — no error, no warning, just silently worse extraction.

Debugging tip — add to ``module_manager.process()`` temporarily:

```python
module_cls = get_module(domain) or BaseModule
print(f"[{self.worker_id}] domain={domain!r} → {module_cls.__name__}")
```

### ❌ Pitfall 3: Returning empty string instead of None

```python
def _extract_content(self, soup):
    paragraphs = [p.get_text(strip=True) for p in container.find_all("p")]
    return "\n\n".join(paragraphs)  # ← returns "" if no paragraphs
```

**Fix:**
```python
    if not paragraphs:
        return None
    return "\n\n".join(paragraphs)
```

### ❌ Pitfall 4: Overriding `extract()` unnecessarily

Override the helpers (`_extract_headline`, `_extract_content`) instead of `extract()`.

### ❌ Pitfall 5: Not calling `super()` in helpers

```python
def _extract_headline(self, soup):
    text = super()._extract_headline(soup)   # ← gets og:title too
    if text:
        return text.replace(" - BBC News", "")
    ...
```

### ❌ Pitfall 6: Forgetting to strip text / losing word boundaries

Always use `.get_text(strip=True)` or `.strip()`.

**When text contains inline `<a>` tags**, use ``separator=" "``:

```python
# BAD — "President<a>Donald Trump</a>said" → "PresidentDonald Trumpsaid"
text = para.get_text(strip=True)

# GOOD — space is inserted where the <a> tag was
text = para.get_text(separator=" ", strip=True)
```

Without ``separator=" "``, BeautifulSoup concatenates adjacent text
nodes with no separator, so words on either side of an inline tag
merge together.

### ❌ Pitfall 7: Not importing in `modules/__init__.py`

Created the file but forgot the import line → decorator never fires.

### ❌ Pitfall 8: Circular imports

Module files must NOT import from `module_manager`, `myredis`, `mypostgres`, or `workers`. Only import from `..base`, `..registry`, `bs4`.

### ❌ Pitfall 9: Overriding `fetch()` but forgetting the `FetchContext` parameter

```python
def fetch(self, url):            # ← BAD: missing context parameter!
    return self._try_browser_fetch(url)
```

**Fix:**
```python
def fetch(self, url, context):   # ← correct signature
    return self._try_browser_fetch(url, context.worker_id)
```

---

## 14. File Checklist for a New Module

When adding a new site (e.g., "CNN"), create/modify these files:

```
fetcher/src/modules/
├── __init__.py           ← ADD: from .CNN.module import CNNModule
├── registry.py           ← (no change)
├── base.py               ← (no change — unless adding shared helpers)
├── services.py           ← (no change — transport layer)
├── module_manager.py     ← (no change — domain routing is automatic)
└── CNN/
    ├── __init__.py       ← CREATE: docstring only
    └── module.py         ← CREATE: CNNModule(BaseModule) + @register_module
```

### Minimal `module.py` template

```python
"""<SiteName> site module."""

from __future__ import annotations

from ..base import BaseModule
from ..registry import register_module


@register_module(domain="<domain>")
class <SiteName>Module(BaseModule):
    """Extraction logic for <site url>."""
    pass
```

### Testing checklist

- [ ] Module appears in `get_all_modules()`
- [ ] `extract()` returns `ArticleData` for a real article page
- [ ] `extract()` returns `None` for a category/index page
- [ ] `is_listing_page()` returns `True` for listing pages
- [ ] `extract_links()` returns URLs under the seed prefix
- [ ] Links don't include fragments, self-references, or `/undefined`
- [ ] `content_hash` is consistent for the same content
- [ ] `fetch()` returns a `BeautifulSoup` for a real URL
- [ ] `fetch()` handles failure gracefully (returns `None`)
- [ ] Custom `fetch()` override accepts `FetchContext` as second parameter
