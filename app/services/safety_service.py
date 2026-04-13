"""Safety checks: duplicate prevention, cooldown, daily volume limits."""
from __future__ import annotations

import re
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ContentItem, ActionLog

logger = logging.getLogger(__name__)


def normalize_text(text: str) -> str:
    """Normalize text for dedup comparison."""
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s]", "", text)
    return text


async def check_duplicate(db: AsyncSession, text: str) -> bool:
    """Check if this exact or normalized text was already published."""
    normalized = normalize_text(text)

    # Check exact match
    result = await db.execute(
        select(ContentItem).where(
            ContentItem.body_text == text,
            ContentItem.status == "published",
        )
    )
    if result.scalar_one_or_none():
        return True

    # Check normalized match against recent items
    recent = (await db.execute(
        select(ContentItem).where(
            ContentItem.status == "published",
            ContentItem.created_at > datetime.now(timezone.utc) - timedelta(days=30),
        )
    )).scalars().all()

    for item in recent:
        if item.body_text and normalize_text(item.body_text) == normalized:
            return True

    return False


async def check_reply_cooldown(db: AsyncSession, target_post_id: str) -> bool:
    """Check if we've replied to this target too recently."""
    cooldown = timedelta(minutes=settings.reply_cooldown_minutes)
    cutoff = datetime.now(timezone.utc) - cooldown

    result = await db.execute(
        select(ContentItem).where(
            ContentItem.target_post_id == target_post_id,
            ContentItem.item_type == "reply",
            ContentItem.created_at > cutoff,
        )
    )
    return result.scalar_one_or_none() is not None


async def check_daily_post_limit(db: AsyncSession) -> tuple[bool, int]:
    """Check if daily post limit has been reached. Returns (limit_reached, count).

    Uses published_at (actual publish time from Threads), not created_at (DB insert time).
    This prevents synced historical posts from counting against today's limit.
    """
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.count()).where(
            ContentItem.item_type == "post",
            ContentItem.status == "published",
            ContentItem.published_at.isnot(None),
            ContentItem.published_at > today_start,
        )
    )
    count = result.scalar() or 0
    return count >= settings.max_posts_per_day, count


async def check_daily_reply_limit(db: AsyncSession) -> tuple[bool, int]:
    """Check if daily reply limit has been reached. Returns (limit_reached, count)."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.count()).where(
            ContentItem.item_type == "reply",
            ContentItem.status == "published",
            ContentItem.published_at.isnot(None),
            ContentItem.published_at > today_start,
        )
    )
    count = result.scalar() or 0
    return count >= settings.max_replies_per_day, count


async def log_action(
    db: AsyncSession,
    event_type: str,
    payload: dict | None = None,
    status: str = "success",
    error_message: str | None = None,
) -> None:
    """Log an action to the audit trail."""
    entry = ActionLog(
        event_type=event_type,
        payload=payload,
        status=status,
        error_message=error_message,
    )
    db.add(entry)
    await db.commit()
