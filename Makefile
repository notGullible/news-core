.PHONY: up down reset reset-redis reset-pg seed scrape test grouper viewer

# ── Infrastructure ──────────────────────────────────────────

up:
	sudo docker compose up

down:
	sudo docker compose down

# ── Reset ───────────────────────────────────────────────────

reset: reset-redis reset-pg
	@echo "==> Redis dedup set + Postgres data cleared. Ready for fresh crawl."

reset-redis:
	cd fetcher && uv run python src/reset_redis.py

reset-pg:
	cd fetcher && uv run python src/reset_postgres.py

reset-pg-bad:
	cd fetcher && uv run python src/reset_postgres.py --bad-only

reset-all:
	cd fetcher && uv run python src/reset_all.py

reset-all-bad:
	cd fetcher && uv run python src/reset_all.py --bad-only

# ── Pipeline ────────────────────────────────────────────────

seed:
	cd fetcher && uv run python src/loader.py

scrape:
	cd fetcher && uv run python src/main.py

# ── Full run ────────────────────────────────────────────────

all: up seed scrape


# Tests
test:
	cd fetcher && uv run python test_reuters_module.py


# Grouper
grouper:
	cd grouper && uv run python src/main.py

# Viewer
viewer:
	cd viewer/src && uv run uvicorn main:app --host 0.0.0.0 --port 8080