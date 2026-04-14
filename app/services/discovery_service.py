"""Discovery service: 6 automated pipelines for finding posts to reply to.

Flow 1: Own Reply — people who replied to YOUR posts (highest value)
Flow 2: Mentions — people who @mentioned you
Flow 3: Conversation Chains — continue existing dialogues
Flow 4: (handled in autopilot_service — engagement posts)
Flow 5: Relationship Tracking — prioritize repeat interactors
Flow 6: Profile Discovery — monitor target accounts for reply opportunities

Plus keyword search using global /keyword_search endpoint.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImportedTarget, ContentItem, UserSettings, KnownAccount
from app.services.threads_client import ThreadsClient, ThreadsAPIError

logger = logging.getLogger(__name__)


async def auto_discover_targets(db: AsyncSession, client: ThreadsClient, user_id: str) -> int:
    """Run ALL discovery pipelines. Returns total new targets found."""
    total = 0

    # Flow 1: Own reply threads (people responding to us — highest value)
    try:
        count = await _discover_own_reply_threads(db, client, user_id)
        total += count
        logger.info("Flow 1 (own_reply): found %d new targets", count)
    except Exception as e:
        logger.warning("Flow 1 (own_reply) failed: %s", e)

    # Flow 2: Mentions (people talking about us)
    try:
        count = await _discover_mentions(db, client, user_id)
        total += count
        logger.info("Flow 2 (mentions): found %d new targets", count)
    except Exception as e:
        logger.warning("Flow 2 (mentions) failed: %s", e)

    # Flow 3: Conversation chains (continue existing dialogues)
    try:
        count = await _discover_conversation_chains(db, client)
        total += count
        logger.info("Flow 3 (chains): found %d new targets", count)
    except Exception as e:
        logger.warning("Flow 3 (chains) failed: %s", e)

    # Keyword search (global /keyword_search endpoint)
    try:
        count = await _discover_by_keywords(db, client)
        total += count
        if count > 0:
            logger.info("Keyword search: found %d new targets", count)
    except Exception as e:
        logger.warning("Keyword search failed: %s", e)

    # Flow 6: Profile discovery — monitor target accounts' recent posts
    try:
        count = await _discover_from_profiles(db, client)
        total += count
        logger.info("Flow 6 (profiles): found %d new targets", count)
    except Exception as e:
        logger.warning("Flow 6 (profiles) failed: %s", e)

    logger.info("Auto-discovery total: %d new targets", total)
    return total


# ──────────────────────────────────────────────
# Flow 1: Own Reply Threads
# ──────────────────────────────────────────────

async def _discover_own_reply_threads(db: AsyncSession, client: ThreadsClient, user_id: str) -> int:
    """Find people who replied to our posts — they're already engaged."""
    recent_posts = (await db.execute(
        select(ContentItem).where(
            ContentItem.item_type == "post",
            ContentItem.status == "published",
            ContentItem.threads_media_id.isnot(None),
        ).order_by(ContentItem.created_at.desc()).limit(10)
    )).scalars().all()

    if not recent_posts:
        logger.info("Flow 1: no published posts in DB")
        return 0

    logger.info("Flow 1: checking replies on %d posts", len(recent_posts))
    new_count = 0

    for post in recent_posts:
        try:
            replies = await client.get_thread_replies(post.threads_media_id)
            logger.info("Flow 1: post %s has %d replies", post.threads_media_id, len(replies))

            for reply in replies:
                media_id = reply.get("id")
                username = reply.get("username", "")
                text = reply.get("text", "")

                if not media_id or not text:
                    continue

                logger.info("Flow 1: reply from @%s: %s", username, text[:80])

                # Skip if already imported
                existing = (await db.execute(
                    select(ImportedTarget).where(ImportedTarget.threads_media_id == media_id)
                )).scalar_one_or_none()
                if existing:
                    continue

                # Skip if we already replied
                already_replied = (await db.execute(
                    select(ContentItem).where(ContentItem.target_post_id == media_id)
                )).scalar_one_or_none()
                if already_replied:
                    continue

                # Track this account (Flow 5)
                await _track_account(db, username, "reply")

                target = ImportedTarget(
                    target_url=reply.get("permalink"),
                    threads_media_id=media_id,
                    body_text_snapshot=text[:2000],
                    source_type="own_reply",
                    import_method="api",
                    topic_tags=["conversation"],
                    relevance_score=0.8,
                )
                db.add(target)
                new_count += 1

        except ThreadsAPIError as e:
            logger.warning("Flow 1: failed for post %s: %s", post.threads_media_id, e.message)

    if new_count > 0:
        await db.commit()
    return new_count


# ──────────────────────────────────────────────
# Flow 2: Mentions
# ──────────────────────────────────────────────

async def _discover_mentions(db: AsyncSession, client: ThreadsClient, user_id: str) -> int:
    """Find posts where user was @mentioned."""
    try:
        mentions = await client.get_mentions(user_id)
    except ThreadsAPIError as e:
        if e.status_code in (400, 403, 500):
            logger.info("Flow 2: mentions API unavailable (%d): %s", e.status_code, e.message)
            return 0
        raise

    logger.info("Flow 2: got %d mentions", len(mentions))
    new_count = 0

    for item in mentions:
        media_id = item.get("id")
        username = item.get("username", "")
        text = item.get("text", "")

        if not media_id or not text:
            continue

        existing = (await db.execute(
            select(ImportedTarget).where(ImportedTarget.threads_media_id == media_id)
        )).scalar_one_or_none()
        if existing:
            continue

        already_replied = (await db.execute(
            select(ContentItem).where(ContentItem.target_post_id == media_id)
        )).scalar_one_or_none()
        if already_replied:
            continue

        await _track_account(db, username, "mention")

        target = ImportedTarget(
            target_url=item.get("permalink"),
            threads_media_id=media_id,
            body_text_snapshot=text[:2000],
            source_type="mention",
            import_method="api",
            topic_tags=["mention"],
            relevance_score=0.9,  # Mentions = highest priority
        )
        db.add(target)
        new_count += 1

    if new_count > 0:
        await db.commit()
    return new_count


# ──────────────────────────────────────────────
# Flow 3: Conversation Chains
# ──────────────────────────────────────────────

async def _discover_conversation_chains(db: AsyncSession, client: ThreadsClient) -> int:
    """Find replies to OUR replies — continue existing dialogues.

    Long threads = more algorithmic visibility on Threads.
    """
    # Get our recent replies (published by us)
    our_replies = (await db.execute(
        select(ContentItem).where(
            ContentItem.item_type == "reply",
            ContentItem.status == "published",
            ContentItem.threads_media_id.isnot(None),
            ContentItem.created_at > datetime.now(timezone.utc) - timedelta(hours=72),
        ).order_by(ContentItem.created_at.desc()).limit(5)
    )).scalars().all()

    if not our_replies:
        logger.info("Flow 3: no recent replies to check for chains")
        return 0

    logger.info("Flow 3: checking %d of our recent replies for chains", len(our_replies))
    new_count = 0

    for our_reply in our_replies:
        try:
            replies = await client.get_thread_replies(our_reply.threads_media_id)
            for reply in replies:
                media_id = reply.get("id")
                username = reply.get("username", "")
                text = reply.get("text", "")

                if not media_id or not text:
                    continue

                existing = (await db.execute(
                    select(ImportedTarget).where(ImportedTarget.threads_media_id == media_id)
                )).scalar_one_or_none()
                if existing:
                    continue

                already_replied = (await db.execute(
                    select(ContentItem).where(ContentItem.target_post_id == media_id)
                )).scalar_one_or_none()
                if already_replied:
                    continue

                await _track_account(db, username, "conversation")

                target = ImportedTarget(
                    target_url=reply.get("permalink"),
                    threads_media_id=media_id,
                    body_text_snapshot=text[:2000],
                    source_type="conversation_chain",
                    import_method="api",
                    topic_tags=["conversation"],
                    relevance_score=0.85,  # Chains are high value
                )
                db.add(target)
                new_count += 1

        except ThreadsAPIError as e:
            logger.warning("Flow 3: failed for reply %s: %s", our_reply.threads_media_id, e.message)

    if new_count > 0:
        await db.commit()
    return new_count


# ──────────────────────────────────────────────
# Keyword Search (bonus, needs App Review)
# ──────────────────────────────────────────────

async def _discover_by_keywords(db: AsyncSession, client: ThreadsClient) -> int:
    """Search by 3 random themes using global /keyword_search endpoint."""
    settings = (await db.execute(select(UserSettings).limit(1))).scalar_one_or_none()
    if not settings or not settings.themes:
        return 0

    themes = list(settings.themes)
    selected = random.sample(themes, min(3, len(themes)))

    new_count = 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    for topic in selected:
        try:
            results = await client.keyword_search(topic, limit=5)
            for item in results:
                media_id = item.get("id")
                if not media_id:
                    continue

                # Skip replies — we want original posts to reply to
                if item.get("is_reply"):
                    continue

                existing = (await db.execute(
                    select(ImportedTarget).where(ImportedTarget.threads_media_id == media_id)
                )).scalar_one_or_none()
                if existing:
                    continue

                # Skip old posts
                ts = item.get("timestamp")
                if ts:
                    try:
                        if datetime.fromisoformat(ts.replace("Z", "+00:00")) < cutoff:
                            continue
                    except (ValueError, TypeError):
                        pass

                username = item.get("username", "")
                if username:
                    await _track_account(db, username, "search")

                target = ImportedTarget(
                    target_url=item.get("permalink"),
                    threads_media_id=media_id,
                    body_text_snapshot=item.get("text", "")[:2000],
                    source_type="keyword_search",
                    import_method="api",
                    topic_tags=[topic],
                    relevance_score=0.5,
                )
                db.add(target)
                new_count += 1

        except ThreadsAPIError as e:
            if e.status_code in (400, 403, 500):
                break  # Permission issue, stop trying
            logger.warning("Keyword search error for '%s': %s", topic, e.message)

    if new_count > 0:
        await db.commit()
    return new_count


# ──────────────────────────────────────────────
# Flow 6: Profile Discovery
# ──────────────────────────────────────────────

async def _discover_from_profiles(db: AsyncSession, client: ThreadsClient) -> int:
    """Monitor target accounts for fresh posts to reply to.

    Uses threads_profile_discovery to read public posts from accounts
    in the user's niche. Great for growth — replying to popular accounts
    gets visibility from their audience.
    """
    settings = (await db.execute(select(UserSettings).limit(1))).scalar_one_or_none()
    if not settings or not settings.target_accounts:
        return 0

    accounts = list(settings.target_accounts)
    # Pick up to 5 random accounts per run to avoid rate limits
    selected = random.sample(accounts, min(5, len(accounts)))

    new_count = 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    for username in selected:
        try:
            posts = await client.get_profile_posts(username, limit=5)
            for item in posts:
                media_id = item.get("id")
                if not media_id:
                    continue

                # Skip replies — we want original posts
                if item.get("is_reply"):
                    continue

                # Skip old posts
                ts = item.get("timestamp")
                if ts:
                    try:
                        if datetime.fromisoformat(ts.replace("Z", "+00:00")) < cutoff:
                            continue
                    except (ValueError, TypeError):
                        pass

                existing = (await db.execute(
                    select(ImportedTarget).where(ImportedTarget.threads_media_id == media_id)
                )).scalar_one_or_none()
                if existing:
                    continue

                already_replied = (await db.execute(
                    select(ContentItem).where(ContentItem.target_post_id == media_id)
                )).scalar_one_or_none()
                if already_replied:
                    continue

                post_username = item.get("username", username)
                await _track_account(db, post_username, "profile_discovery")

                target = ImportedTarget(
                    target_url=item.get("permalink"),
                    threads_media_id=media_id,
                    body_text_snapshot=item.get("text", "")[:2000],
                    source_type="profile_discovery",
                    import_method="api",
                    topic_tags=[f"@{post_username}"],
                    relevance_score=0.7,  # Good value — popular accounts have engaged audiences
                )
                db.add(target)
                new_count += 1

        except ThreadsAPIError as e:
            if e.status_code in (400, 403):
                logger.info("Flow 6: profile '%s' unavailable (%d): %s", username, e.status_code, e.message)
                continue
            logger.warning("Flow 6: error for '%s': %s", username, e.message)

    if new_count > 0:
        await db.commit()
    return new_count


# ──────────────────────────────────────────────
# Flow 5: Relationship Tracking
# ──────────────────────────────────────────────

async def _track_account(db: AsyncSession, username: str, source: str) -> None:
    """Track or update a known account. More interactions = higher priority."""
    if not username:
        return

    account = (await db.execute(
        select(KnownAccount).where(KnownAccount.username == username)
    )).scalar_one_or_none()

    if account:
        account.interaction_count += 1
        account.last_seen_at = datetime.now(timezone.utc)
    else:
        account = KnownAccount(
            username=username,
            interaction_count=1,
            source=source,
        )
        db.add(account)


async def get_known_account_bonus(db: AsyncSession, username: str) -> float:
    """Get priority bonus for a known account. Returns 0.0-0.3 bonus score."""
    if not username:
        return 0.0
    account = (await db.execute(
        select(KnownAccount).where(KnownAccount.username == username)
    )).scalar_one_or_none()
    if not account:
        return 0.0
    # More interactions = higher bonus, capped at 0.3
    return min(0.3, account.interaction_count * 0.1)


# Legacy alias
async def run_keyword_discovery(db: AsyncSession, client: ThreadsClient, user_id: str = "me") -> int:
    return await _discover_by_keywords(db, client)
