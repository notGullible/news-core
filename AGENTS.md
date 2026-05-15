# Pipeline

## Structure

- `fetcher/` — single Python package, the only app code. Not a Python package registry package; it's a service.
- `typings/botasaurus/` — locally vendored type stubs for `botasaurus` (gitignored). The dependency has poor type hints, so `# type: ignore` is used throughout `main.py` and the stubs provide IDE support only.
- `compose.yaml` — Docker Compose for Redis (required runtime dependency).

## Requirements

- **Python 3.14** (pinned in `.python-version` and `pyproject.toml`)
- **uv** — package manager (lockfile is `uv.lock`, not pip)
- **Redis** — must be running before the service starts. Use `docker compose up -d` from the repo root.

## Run Order

1. `docker compose up -d` — start Redis
2. `cd fetcher && uv run python loader.py` — populate the Redis stream with sites to scrape
3. `cd fetcher && uv run python main.py` — start the fetcher service (consumes from Redis stream)

`main.py` will fail immediately if Redis is unreachable.

## Commands

```sh
# Install dependencies
cd fetcher && uv sync

# Run the fetcher service
cd fetcher && uv run python main.py

# Load sites into the queue
cd fetcher && uv run python loader.py
```

## Conventions

- No test suite, no linter, no type checker configured.
- `botasaurus` uses a decorator-driven API (`@request`, `@browser`, etc.) that produces opaque types — `# type: ignore` is expected and necessary.
- `config.py` holds all runtime config (Redis host/port/stream name) as module-level constants.