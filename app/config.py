from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _fix_db_url(url: str) -> str:
    """Convert Railway's postgresql:// to asyncpg-compatible URL.

    Railway provides DATABASE_URL as postgresql://user:pass@host:5432/db
    SQLAlchemy async requires postgresql+asyncpg://user:pass@host:5432/db
    """
    if not url:
        raise ValueError(
            "DATABASE_URL is not set. "
            "On Railway: add DATABASE_URL=${{Postgres.DATABASE_URL}} to your service variables. "
            "Locally: set DATABASE_URL in .env file."
        )
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


@dataclass(frozen=True)
class Settings:
    # Database (auto-converts Railway's postgresql:// to postgresql+asyncpg://)
    database_url: str = field(default_factory=lambda: _fix_db_url(os.environ.get("DATABASE_URL", "")))

    # Threads API
    threads_app_id: str = field(default_factory=lambda: os.environ.get("THREADS_APP_ID", ""))
    threads_app_secret: str = field(default_factory=lambda: os.environ.get("THREADS_APP_SECRET", ""))
    threads_redirect_uri: str = field(default_factory=lambda: os.environ.get("THREADS_REDIRECT_URI", "http://localhost:8000/auth/callback"))

    # LLM (OpenAI-compatible — works with OpenRouter, OpenAI, etc.)
    llm_api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", ""))
    llm_base_url: str = field(default_factory=lambda: os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1"))
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "deepseek/deepseek-chat"))

    # App
    secret_key: str = field(default_factory=lambda: os.environ.get("SECRET_KEY", "change-me"))
    admin_password: str = field(default_factory=lambda: os.environ.get("ADMIN_PASSWORD", "changeme"))
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))

    # Safety limits
    max_posts_per_day: int = 5
    max_replies_per_day: int = 15
    max_searches_per_day: int = 50
    max_llm_requests_per_day: int = 20
    reply_cooldown_minutes: int = 30

    # Autopilot
    autopilot_post_interval_hours: int = 3
    autopilot_reply_interval_hours: int = 1
    autopilot_min_score: float = 0.6


settings = Settings()
