"""
Transport layer — lightweight wrappers around botasaurus ``@request`` (HTTP)
and ``@browser`` (headless Chrome).

Site modules should **not** subclass these.  The :class:`ModuleManager`
calls the helpers below to fetch page content, then passes the resulting
BeautifulSoup object to the module's ``extract()`` / ``extract_links()``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import botasaurus.decorators_common  # type: ignore[import-untyped]
from botasaurus.request import request  # type: ignore[import-untyped]
from botasaurus.browser import browser, Driver  # type: ignore[import-untyped]
from botasaurus.soupify import soupify  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from bs4 import BeautifulSoup

# Prevent the "Running…" banner from printing on every decorator call.
botasaurus.decorators_common.first_run = True

log = logging.getLogger(__name__)


# ── HTTP (lightweight) ────────────────────────────────────────────────


def http_fetch(url: str) -> BeautifulSoup | None:
    """Fetch *url* via a plain HTTP GET and return the parsed HTML.

    Returns ``None`` on failure.
    """
    try:

        @request(output=None)  # type: ignore[misc]
        def _fetch(req, _data):  # type: ignore[no-untyped-def]
            response = req.get(url)  # type: ignore[no-untyped-call]
            return soupify(response)  # type: ignore[no-untyped-call]

        return _fetch()  # type: ignore[no-untyped-call]
    except Exception:
        log.exception("HTTP fetch failed for %s", url)
        return None


# ── Browser (headless Chrome) ─────────────────────────────────────────


def browser_fetch(url: str, worker_id: int) -> BeautifulSoup | None:
    """Fetch *url* via a headless Chrome browser and return the parsed HTML.

    Returns ``None`` on failure.  The browser is automatically closed after
    the call completes.
    """
    try:

        @browser(  # type: ignore[misc]
            output=None,
            headless=True,
            wait_for_complete_page_load=True,
        )
        def _scrap(driver: Driver, _data):  # type: ignore[no-untyped-def]
            log.info("  [Worker %s] Browser navigating to %s", worker_id, url)
            driver.get(url)  # type: ignore[no-untyped-call]
            driver.short_random_sleep()  # type: ignore[no-untyped-call]
            return soupify(driver)  # type: ignore[no-untyped-call]

        return _scrap()  # type: ignore[no-untyped-call]
    except Exception:
        log.exception("Browser fetch failed for %s", url)
        return None
