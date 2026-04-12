"""Daily sync orchestration: content, metrics, discovery, recommendations."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ContentItem, MetricsSnapshot, SyncLog, OAuthToken
from app.services.threads_client import ThreadsClient, ThreadsAPIError
from app.services.token_manager import check_and_refresh_token, get_active_token
from app.services.discovery_service import run_keyword_discovery
from app.services.analytics_service import generate_recommendations

logger = logging.getLogger(__name__)


async def _log_sync(db: AsyncSession, sync_type: str, status: str, items: int = 0, errors: dict | None = None) -> SyncLog:
    entry = SyncLog(
        sync_type=sync_type,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        status=status,
        items_processed=items,
        errors=errors,
    )
    db.add(entry)
    await db.commit()
    return entry


async def sync_own_content(db: AsyncSession, client: ThreadsClient, user_id: str) -> int:
    """Sync user's own published posts from Threads API."""
    try:
        threads = await client.get_user_threads(user_id, limit=25)
    except ThreadsAPIError as e:
        logger.error("Content sync failed: %s", e)
        await _log_sync(db, "content", "error", errors={"message": e.message})
        return 0

    new_count = 0
    for thread in threads:
        media_id = thread.get("id")
        if not media_id:
            continue

        existing = (await db.execute(
            select(ContentItem).where(ContentItem.threads_media_id == media_id)
        )).scalar_one_or_none()

        if existing:
            continue

        item = ContentItem(
            threads_media_id=media_id,
            url=thread.get("permalink"),
            item_type="reply" if thread.get("is_reply") else "post",
            body_text=thread.get("text", "")[:5000],
            status="published",
            published_at=datetime.fromisoformat(thread["timestamp"]) if thread.get("timestamp") else None,
        )
        db.add(item)
        new_count += 1

    await db.commit()
    await _log_sync(db, "content", "success", new_count)
    return new_count


async def sync_metrics(db: AsyncSession, client: ThreadsClient) -> int:
    """Sync metrics for recent published content."""
    recent = (await db.execute(
        select(ContentItem)
        .where(ContentItem.status == "published", ContentItem.threads_media_id.isnot(None))
        .order_by(ContentItem.created_at.desc())
        .limit(20)
    )).scalars().all()

    synced = 0
    for item in recent:
        try:
            insights = await client.get_media_insights(item.threads_media_id)
            snapshot = MetricsSnapshot(
                content_item_id=item.id,
                views=insights.get("views", 0),
                likes=insights.get("likes", 0),
                replies=insights.get("replies", 0),
                reposts=insights.get("reposts", 0),
                quotes=insights.get("quotes", 0),
                shares=insights.get("shares", 0),
            )
            db.add(snapshot)
            synced += 1
        except ThreadsAPIError as e:
            logger.warning("Metrics sync failed for %s: %s", item.threads_media_id, e.message)

    await db.commit()
    await _log_sync(db, "metrics", "success", synced)
    return synced


async def run_full_sync(db: AsyncSession) -> dict:
    """Run the complete daily sync sequence."""
    results = {}

    # Step 1: Token check/refresh
    token_result = await check_and_refresh_token(db)
    results["token"] = token_result
    await _log_sync(db, "token_refresh", token_result.get("status", "unknown"))

    if token_result["status"] in ("no_token", "expired"):
        logger.error("Cannot sync: %s", token_result["message"])
        return results

    # Get token and create client
    token = await get_active_token(db)
    if not token:
        return results

    client = ThreadsClient(token.access_token)
    user_id = token.threads_user_id or "me"

    try:
        # Step 2: Content sync
        content_count = await sync_own_content(db, client, user_id)
        results["content_synced"] = content_count

        # Step 3: Metrics sync
        metrics_count = await sync_metrics(db, client)
        results["metrics_synced"] = metrics_count

        # Step 4: Discovery (keyword search)
        try:
            discovery_count = await run_keyword_discovery(db, client)
            results["discovery_found"] = discovery_count
        except Exception as e:
            logger.warning("Discovery failed (may need App Review): %s", e)
            results["discovery_found"] = 0
            await _log_sync(db, "discovery", "error", errors={"message": str(e)})

        # Step 5: Generate recommendations
        try:
            rec_count = await generate_recommendations(db)
            results["recommendations_generated"] = rec_count
        except Exception as e:
            logger.error("Recommendation generation failed: %s", e)
            results["recommendations_generated"] = 0

    finally:
        await client.close()

    return results
