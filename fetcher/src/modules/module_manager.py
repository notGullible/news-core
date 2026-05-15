"""Routes an incoming scrape task to the correct module."""

from __future__ import annotations

from urllib.parse import urlparse

from .Reuters.fetch import ReutersFetcher
from .Reuters.scrap import ReutersScrapper


class ModuleManager:
    def __init__(self, worker_id: int) -> None:
        self.worker_id = worker_id

    def fetch(self, data: dict) -> dict | None:
        """Dispatch a task to the right module based on the site URL.

        ``data`` is the payload dequeued from Redis, e.g.
        ``{"site": "https://www.reuters.com/..."}``.
        """
        # -- resolve the actual URL ----------------------------------------
        raw_link: str | None = data.get("site") if isinstance(data, dict) else None
        if not raw_link:
            return {"error": "Missing 'site' key in task payload"}

        link = raw_link.strip()
        if not link:
            return {"error": "Empty site URL"}

        domain = urlparse(link).netloc.lower()

        # -- route ---------------------------------------------------------
        if "reuters.com" in domain:
            # Reuters requires JavaScript → use browser-based Scrapper.
            scraper = ReutersScrapper(worker_id=self.worker_id, link=link)
            return scraper.scrap()

        # Fallback / unknown site: try lightweight request-based fetch.
        # (ReutersFetcher will detect JS-only pages and return an error.)
        fetcher = ReutersFetcher(worker_id=self.worker_id, link=link)
        return fetcher.fetch()



