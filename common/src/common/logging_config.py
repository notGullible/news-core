"""
Universal logging configuration for the NG pipeline services.

Configures the root logger with a custom formatter that produces:

    timestamp |   [Component][id] | [url] message

Two handlers are installed per process:
- StreamHandler → stdout (terminal / docker logs)
- FileHandler   → logs/{timestamp}/main.log or worker-{N}.log

A background thread flushes the file handler every *flush_interval*
seconds.  Call ``flusher.stop()`` on graceful shutdown to force a final
flush and join the thread.

Usage::

    from common.logging_config import setup_logging
    import logging

    flusher = setup_logging(worker_id=3, component="Fetcher")
    log = logging.getLogger(__name__)
    log.info("Task started", extra={"url": "https://..."})
    ...
    flusher.stop()
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from common.config import LOG_LEVEL, LOG_DIR, LOG_FLUSH_INTERVAL

# ── Format strings ─────────────────────────────────────────────────
LOG_FMT = "%(asctime)s |   %(prefix)s%(message)s"
DATE_FMT = "%Y-%m-%d %H:%M:%S"

# ── Per-process state (set by setup_logging) ──────────────────────
_current_worker_id: int = -1
_current_component: str = "Pipeline"
_configured: bool = False  # guards against re-configuration within the same process


def set_worker_id(worker_id: int) -> None:
    """Store the worker-id so the formatter can pick it up as a default."""
    global _current_worker_id
    _current_worker_id = worker_id


# ── Custom formatter ───────────────────────────────────────────────


class PipelineFormatter(logging.Formatter):
    """Injects ``[Component][id] | [url]`` before every message.

    ``worker_id`` is read from ``record.worker_id`` (set via ``extra=``)
    and falls back to the process-global ``_current_worker_id``.

    ``component`` is read from ``record.component`` (set via ``extra=``)
    and falls back to ``_current_component``.

    ``url`` is read from ``record.url`` (set via ``extra=``) and
    falls back to ``"-"`` when absent.
    """

    def format(self, record: logging.LogRecord) -> str:
        worker_id = getattr(record, "worker_id", None)
        if worker_id is None:
            worker_id = _current_worker_id
        component = getattr(record, "component", None)
        if component is None:
            component = _current_component
        url = getattr(record, "url", "-")
        record.prefix = f"[{component}][{worker_id}] | [{url}] "  # type: ignore[attr-defined]
        return super().format(record)


# ── Periodic flush background thread ───────────────────────────────


class _PeriodicFlusher:
    """Calls ``handler.flush()`` every *interval* seconds."""

    @staticmethod
    def _noop() -> _PeriodicFlusher:
        """Return a no-op flusher for idempotent ``setup_logging`` calls."""
        return _NoopFlusher()  # type: ignore[return-value]

    def __init__(self, handler: logging.FileHandler, interval: int = 60) -> None:
        self._handler = handler
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(interval,), daemon=True
        )
        self._thread.start()

    def _run(self, interval: int) -> None:
        while not self._stop.wait(interval):
            try:
                self._handler.flush()
            except Exception:
                pass

    def stop(self) -> None:
        """Signal the thread to stop and perform a final flush."""
        self._stop.set()
        self._thread.join(timeout=2)
        try:
            self._handler.flush()
        except Exception:
            pass


class _NoopFlusher(_PeriodicFlusher):
    """No-op flusher returned on duplicate ``setup_logging`` calls."""

    def __init__(self) -> None:
        pass  # skip thread creation entirely

    def stop(self) -> None:
        pass  # nothing to flush


# ── Public API ─────────────────────────────────────────────────────


def setup_logging(
    worker_id: int,
    component: str = "Pipeline",
) -> _PeriodicFlusher:
    """Configure universal logging for the current OS process.

    Creates ``logs/{timestamp}/``, installs the formatter + handlers
    on the root logger, starts the periodic flusher.

    *worker_id* is 0..N for workers; use -1 for the main process.
    *component* is the service name shown in log prefixes (e.g. "Fetcher", "Grouper").

    Idempotent: subsequent calls only update the worker-id / component
    and return a no-op flusher (the first caller owns the real one).
    """
    global _configured, _current_component, _current_worker_id

    _current_worker_id = worker_id
    _current_component = component

    if _configured:
        # Already set up — return a no-op flusher.
        return _PeriodicFlusher._noop()  # type: ignore[return-value]
    _configured = True

    # Timestamped log directory (one per run).
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    log_dir = Path.cwd() / LOG_DIR / ts
    os.makedirs(log_dir, exist_ok=True)

    log_file = "main.log" if worker_id < 0 else f"worker-{worker_id}.log"
    file_path = log_dir / log_file

    level = getattr(logging, LOG_LEVEL, logging.INFO)
    formatter = PipelineFormatter(fmt=LOG_FMT, datefmt=DATE_FMT)

    # Stream handler → stdout.
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream.setLevel(level)

    # File handler → disk.
    file_handler = logging.FileHandler(str(file_path))
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    # Replace root logger configuration.
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(stream)
    root.addHandler(file_handler)

    flusher = _PeriodicFlusher(file_handler, interval=LOG_FLUSH_INTERVAL)

    return flusher
