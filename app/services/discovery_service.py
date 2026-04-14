"""Discovery service: auto-find posts to reply to.

Two sources:
1. Keyword search — find public posts matching user's themes
2. Own reply threads — people who replied to YOUR posts (highest value)
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImportedTarget, ContentItem, UserSettings
from app.services.threads_client import ThreadsClient, ThreadsAPIError

logger = logging.getLogger(__name__)


async def auto_discover_targets(db: AsyncSession, client: ThreadsClient, user_id: str) -> int:
    """Run all discovery pipelines. Returns total new targets found."""
    total = 0

    # 1. Keyword search (find fresh public posts by theme)
    try:
        count = await _discover_by_keywords(db, client)
        total += count
    except Exception as e:
        logger.warning("Keyword discovery failed: %s", e)

    # 2. Replies on own posts (people engaging with us — highest value)
    try:
        count = await _discover_own_reply_threads(db, client, user_id)
        total += count
    except Exception as e:
        logger.warning("Own reply discovery failed: %s", e)

    logger.info("Auto-discovery found %d new targets", total)
    return total


async def _discover_by_keywords(db: AsyncSession, client: ThreadsClient) -> int:
    """Search for fresh posts by 3 random user themes."""
    settings = (await db.execute(select(UserSettings).limit(1))).scalar_one_or_none()
    if not settings or not settings.themes:
        return 0

    # Pick 3 random themes (not always the same ones)
    themes = list(settings.themes)
    sample_size = min(3, len(themes))
    selected_themes = random.sample(themes, sample_size)

    new_count = 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    for topic_name in selected_themes:
        try:
            results = await client.keyword_search(topic_name, limit=5)
            for item in results:
                media_id = item.get("id")
                if not media_id:
                    continue

                # Skip if already imported
                existing = (await db.execute(
                    select(ImportedTarget).where(ImportedTarget.threads_media_id == media_id)
                )).scalar_one_or_none()
                if existing:
                    continue

                # Skip very old posts (focus on fresh conversations)
                timestamp = item.get("timestamp")
                if timestamp:
                    try:
                        post_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        if post_time < cutoff:
                            continue
                    except (ValueError, TypeError):
                        pass

                target = ImportedTarget(
                    target_url=item.get("permalink"),
                    threads_media_id=media_id,
                    body_text_snapshot=item.get("text", "")[:2000],
                    source_type="keyword_search",
                    import_method="api",
                    topic_tags=[topic_name],
                    relevance_score=0.5,
                )
                db.add(target)
                new_count += 1

        except ThreadsAPIError as e:
            if e.status_code == 403:
                logger.info("Keyword search needs App Review, skipping")
                break
            logger.warning("Keyword search failed for '%s': %s", topic_name, e.message)

    if new_count > 0:
        await db.commit()
    return new_count


async def _discover_own_reply_threads(db: AsyncSession, client: ThreadsClient, user_id: str) -> int:
    """Find people who replied to our recent posts — they're already engaged.

    These are the highest-value targets because:
    1. They're already in conversation with us
    2. Replying back builds relationships
    3. Their followers see the thread
    """
    # Get our recent posts
    recent_posts = (await db.execute(
        select(ContentItem).where(
            ContentItem.item_type == "post",
            ContentItem.status == "published",
            ContentItem.threads_media_id.isnot(None),
        ).order_by(ContentItem.created_at.desc()).limit(5)
    )).scalars().all()

    if not recent_posts:
        return 0

    new_count = 0
    for post in recent_posts:
        try:
            replies = await client.get_thread_replies(post.threads_media_id)
            for reply in replies:
                media_id = reply.get("id")
                if not media_id:
                    continue

                # Skip our own replies
                username = reply.get("username", "")
                # Skip if already imported
                existing = (await db.execute(
                    select(ImportedTarget).where(ImportedTarget.threads_media_id == media_id)
                )).scalar_one_or_none()
                if existing:
                    continue

                # Skip if we already replied to this
                already_replied = (await db.execute(
                    select(ContentItem).where(ContentItem.target_post_id == media_id)
                )).scalar_one_or_none()
                if already_replied:
                    continue

                target = ImportedTarget(
                    target_url=reply.get("permalink"),
                    threads_media_id=media_id,
                    body_text_snapshot=reply.get("text", "")[:2000],
                    source_type="own_reply",
                    import_method="api",
                    topic_tags=["conversation"],
                    relevance_score=0.8,  # Higher score — these people are already engaged
                )
                db.add(target)
                new_count += 1

        except ThreadsAPIError as e:
            logger.warning("Failed to get replies for %s: %s", post.threads_media_id, e.message)

    if new_count > 0:
        await db.commit()
    return new_count


# Legacy function for daily sync compatibility
async def run_keyword_discovery(db: AsyncSession, client: ThreadsClient) -> int:
    return await _discover_by_keywords(db, client)
