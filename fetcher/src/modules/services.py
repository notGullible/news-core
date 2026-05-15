import logging


class Fetcher:
    """Base class for request-based (lightweight HTTP) fetchers.

    Uses botasaurus ``@request`` decorator under the hood.
    Subclasses override :meth:`fetch` and decorate an inner function.
    """

    def __init__(self, worker_id: int, link: str):
        logging.basicConfig(
            level=logging.INFO,
            format=f"   %(asctime)s | %(message)s | [{worker_id}] | ",
        )
        self.log = logging.getLogger(__name__)
        self.worker_id = worker_id
        self.link = link

    def fetch(self):
        """Override in subclass. Must return extracted data or None on failure."""
        raise NotImplementedError("Subclasses must implement fetch()")


class Scrapper:
    """Base class for browser-based (JavaScript-rendered) scrapper.

    Uses botasaurus ``@browser`` decorator under the hood.
    Slower and heavier than :class:`Fetcher` — use only when the target
    site requires JavaScript execution.

    Subclasses override :meth:`scrap` and decorate an inner function.
    """

    def __init__(self, worker_id: int, link: str):
        logging.basicConfig(
            level=logging.INFO,
            format=f"   %(asctime)s | %(message)s | [{worker_id}] | ",
        )
        self.log = logging.getLogger(__name__)
        self.worker_id = worker_id
        self.link = link

    def scrap(self):
        """Override in subclass. Must return extracted data or None on failure."""
        raise NotImplementedError("Subclasses must implement scrap()")