"""Seed default UserSettings if empty.

Runs idempotently: does nothing if settings already exist.
Usage: python -m scripts.seed
Also called automatically on app startup.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserSettings

logger = logging.getLogger(__name__)


DEFAULT_SETTINGS = {
    "positioning": (
        "Senior software architect shipping AI products in production. I build scalable backend systems, "
        "design agent orchestration pipelines, and ship startup MVPs that actually survive contact with "
        "real users. 10+ years turning messy problems into working software. I care about execution over "
        "theory, shipping over planning, and clarity over cleverness."
    ),
    "themes": [
        "AI agents",
        "LLM orchestration",
        "production AI",
        "startup execution",
        "backend architecture",
        "product thinking",
        "developer tools",
        "system design",
        "AI engineering",
        "indie hacking",
    ],
    "desired_audience": (
        "Developers building with LLMs, founders shipping AI products, engineering leaders scaling teams, "
        "and technical indie hackers who prefer shipping over theorizing. People who've felt the pain of "
        "production bugs at 2am and know the difference between a demo and a real system."
    ),
    "writing_style": (
        "Sharp, specific, opinionated. Short sentences. Concrete examples over abstract claims. "
        "First-person when sharing experience, never hedging with 'I think'. Contrarian when the "
        "consensus is lazy. No emoji spam. No corporate speak. No 'unlock', 'leverage', 'synergy'. "
        "Lead with the sharpest take, then explain. Avoid lists unless the structure genuinely helps. "
        "Write like you talk to a senior engineer friend — direct, curious, no fluff."
    ),
    "forbidden_themes": [
        "politics",
        "religion",
        "personal finance advice",
        "crypto pumping",
        "motivational quotes",
        "engagement bait questions",
        "life hacks",
    ],
    "target_accounts": [
        "zaboravsky",
        "maboroshi_ai",
        "ai_pub",
        "techcrunch",
        "openai",
        "anthropic",
        "googleai",
        "huggingface",
        "langaboratory",
        "ycombinator",
    ],
    "daily_post_target": 5,
    "daily_reply_target": 15,
    "growth_goal": (
        "Reach 5000 engaged followers in 6 months by posting 3 high-signal insights per day and joining "
        "10 meaningful conversations. Build a reputation as the person developers follow for honest takes "
        "on AI engineering and startup execution — not hype. Optimize for conversations, not impressions. "
        "Measure success by reply quality and inbound DMs from interesting people, not vanity metrics."
    ),
}


async def seed_user_settings(db: AsyncSession) -> UserSettings:
    """Create default UserSettings if none exist. Returns the current settings."""
    existing = (await db.execute(select(UserSettings).limit(1))).scalar_one_or_none()
    if existing:
        logger.info("UserSettings already exist (id=%s), skipping seed", existing.id)
        return existing

    settings = UserSettings(**DEFAULT_SETTINGS)
    db.add(settings)
    await db.commit()
    await db.refresh(settings)
    logger.info("Seeded default UserSettings (id=%s)", settings.id)
    return settings


async def _main():
    logging.basicConfig(level=logging.INFO)
    from dotenv import load_dotenv
    load_dotenv()
    from app.db import async_session, engine

    async with async_session() as db:
        await seed_user_settings(db)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
