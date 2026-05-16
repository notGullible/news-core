"""Runtime configuration — sourced from .env or environment."""

import os
from dotenv import load_dotenv

load_dotenv()

POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB: str = os.getenv("POSTGRES_DB", "fetcher")
POSTGRES_USER: str = os.getenv("POSTGRES_USER", "fetcher")
POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "fetcher")

PAGE_SIZE: int = int(os.getenv("PAGE_SIZE", "25"))
