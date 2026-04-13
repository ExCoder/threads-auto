"""Autonomous agent: decides what to do and publishes automatically."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    AgentRun, ContentItem, Draft, ImportedTarget,
    Recommendation, UserSettings, OAuthToken,
)
from app.services.drafting_service import generate_post_drafts, generate_reply_drafts, pick_best_variant
from app.services.safety_service import check_duplicate, check_reply_cooldown, check_daily_post_limit, check_daily_reply_limit, log_action
from app.services.token_manager import get_active_token, check_and_refresh_token
from app.services.threads_client import ThreadsClient, ThreadsAPIError

logger = logging.getLogger(__name__)

MIN_SCORE = settings.autopilot_min_score


async def run_autopilot(db: AsyncSession) -> AgentRun:
    """Run one autonomous agent cycle. Returns the AgentRun record."""
    run = AgentRun(decision="skip", status="running")
    db.add(run)
    await db.commit()
    await db.refresh(run)

    try:
        result = await _execute(db, run)
        return result
    except Exception as e:
        logger.error("Autopilot unexpected error: %s", e, exc_info=True)
        run.status = "error"
        run.error_message = str(e)[:500]
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        return run


async def _execute(db: AsyncSession, run: AgentRun) -> AgentRun:
    now = datetime.now(timezone.utc)

    # --- Pre-flight checks ---

    # 1. Autopilot enabled?
    user_settings = (await db.execute(select(UserSettings).limit(1))).scalar_one_or_none()
    if not user_settings or not user_settings.autopilot_enabled:
        return await _skip(db, run, "autopilot_disabled")

    # 2. Settings filled?
    if not user_settings.positioning or not user_settings.themes:
        return await _skip(db, run, "settings_incomplete")

    # 3. Token valid?
    token_result = await check_and_refresh_token(db)
    if token_result["status"] in ("no_token", "expired"):
        return await _skip(db, run, f"token_{token_result['status']}")

    token = await get_active_token(db)
    if not token:
        return await _skip(db, run, "no_token")

    # 4. Overlap check — skip if another run is still "running" from last 10 min
    recent_running = (await db.execute(
        select(AgentRun).where(
            AgentRun.id != run.id,
            AgentRun.status == "running",
            AgentRun.started_at > now - timedelta(minutes=10),
        )
    )).scalar_one_or_none()
    if recent_running:
        return await _skip(db, run, "concurrent_run")

    # --- Capacity ---
    post_limit_reached, post_count = await check_daily_post_limit(db)
    reply_limit_reached, reply_count = await check_daily_reply_limit(db)
    run.posts_today = post_count
    run.replies_today = reply_count

    if post_limit_reached and reply_limit_reached:
        return await _skip(db, run, "all_daily_limits_reached")

    # --- Generate recommendations if none exist ---
    from app.services.analytics_service import generate_recommendations
    unconsumed_count = (await db.execute(
        select(Recommendation).where(Recommendation.consumed == False).limit(1)
    )).scalar_one_or_none()
    if not unconsumed_count:
        logger.info("No recommendations found, generating fresh ones...")
        await generate_recommendations(db)

    # --- Pick best action ---
    cutoff = now - timedelta(hours=48)

    best_post_rec = None
    if not post_limit_reached:
        best_post_rec = (await db.execute(
            select(Recommendation).where(
                Recommendation.rec_type == "post_idea",
                Recommendation.consumed == False,
                Recommendation.created_at > cutoff,
                Recommendation.score >= MIN_SCORE,
            ).order_by(Recommendation.score.desc()).limit(1)
        )).scalar_one_or_none()

    best_reply_rec = None
    if not reply_limit_reached:
        # Only pick reply opportunities whose target has a valid threads_media_id
        # (manual_paste imports without media_id can't be auto-replied via API)
        from sqlalchemy import and_, exists
        best_reply_rec = (await db.execute(
            select(Recommendation).where(
                Recommendation.rec_type == "reply_opportunity",
                Recommendation.consumed == False,
                Recommendation.created_at > cutoff,
                Recommendation.source_target_id.isnot(None),
                exists().where(and_(
                    ImportedTarget.id == Recommendation.source_target_id,
                    ImportedTarget.threads_media_id.isnot(None),
                )),
            ).order_by(Recommendation.score.desc()).limit(1)
        )).scalar_one_or_none()

    # Decision logic
    action = None
    chosen_rec = None

    if best_post_rec and best_reply_rec:
        if best_reply_rec.score > best_post_rec.score:
            action, chosen_rec = "reply", best_reply_rec
        else:
            action, chosen_rec = "post", best_post_rec
    elif best_post_rec:
        action, chosen_rec = "post", best_post_rec
    elif best_reply_rec:
        action, chosen_rec = "reply", best_reply_rec
    else:
        return await _skip(db, run, "no_recommendations")

    run.decision = action
    run.recommendation_id = chosen_rec.id
    run.decision_reason = f"{action}: \"{chosen_rec.title[:60]}\" score={chosen_rec.score:.2f}"

    # --- Execute action ---
    client = ThreadsClient(token.access_token)
    user_id = token.threads_user_id or "me"

    try:
        if action == "post":
            await _do_post(db, run, chosen_rec, client, user_id, user_settings)
        else:
            await _do_reply(db, run, chosen_rec, client, user_id, user_settings)
    finally:
        await client.close()

    return run


async def _do_post(db, run, rec, client, user_id, user_settings):
    # Generate drafts
    draft = await generate_post_drafts(db, rec.title)
    run.draft_id = draft.id

    # Pick best variant
    best_idx = await pick_best_variant(draft.variants, rec.title, "post")
    text = draft.variants[best_idx]
    run.chosen_variant_index = best_idx
    run.chosen_variant_text = text

    # Safety checks
    if await _check_forbidden(text, user_settings):
        return await _skip(db, run, "forbidden_theme_detected")

    if await check_duplicate(db, text):
        return await _skip(db, run, "duplicate")

    # Publish
    try:
        media_id = await client.publish_text_post(user_id, text)
    except ThreadsAPIError as e:
        run.status = "error"
        run.error_message = e.message[:500]
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        await log_action(db, "autopilot_post_failed", {"error": e.message}, "error", e.message)
        return

    # Record success
    content_item = ContentItem(
        threads_media_id=media_id,
        item_type="post",
        body_text=text,
        status="published",
        published_at=datetime.now(timezone.utc),
    )
    db.add(content_item)
    await db.flush()

    draft.approval_status = "published"
    draft.chosen_variant_index = best_idx
    draft.content_item_id = content_item.id
    rec.consumed = True

    run.content_item_id = content_item.id
    run.threads_media_id = media_id
    run.published_url = f"https://www.threads.net/post/{media_id}"
    run.status = "success"
    run.finished_at = datetime.now(timezone.utc)
    await db.commit()

    await log_action(db, "autopilot_post", {"media_id": media_id, "text": text[:200]})


async def _do_reply(db, run, rec, client, user_id, user_settings):
    # Find target
    target = None
    if rec.source_target_id:
        target = (await db.execute(
            select(ImportedTarget).where(ImportedTarget.id == rec.source_target_id)
        )).scalar_one_or_none()

    if not target or not target.threads_media_id:
        return await _skip(db, run, "no_reply_target")

    run.imported_target_id = target.id

    # Cooldown check
    if await check_reply_cooldown(db, target.threads_media_id):
        return await _skip(db, run, "reply_cooldown")

    # Generate reply drafts
    draft = await generate_reply_drafts(db, rec.title, target.id)
    run.draft_id = draft.id

    # Pick best variant
    best_idx = await pick_best_variant(draft.variants, rec.title, "reply")
    text = draft.variants[best_idx]
    run.chosen_variant_index = best_idx
    run.chosen_variant_text = text

    # Safety checks
    if await _check_forbidden(text, user_settings):
        return await _skip(db, run, "forbidden_theme_detected")

    if await check_duplicate(db, text):
        return await _skip(db, run, "duplicate")

    # Publish reply
    try:
        media_id = await client.publish_reply(user_id, text, target.threads_media_id)
    except ThreadsAPIError as e:
        run.status = "error"
        run.error_message = e.message[:500]
        run.finished_at = datetime.now(timezone.utc)
        target.relevance_score = 0.0  # Don't retry failed targets
        await db.commit()
        await log_action(db, "autopilot_reply_failed", {"error": e.message}, "error", e.message)
        return

    # Record success
    content_item = ContentItem(
        threads_media_id=media_id,
        item_type="reply",
        body_text=text,
        target_post_id=target.threads_media_id,
        status="published",
        published_at=datetime.now(timezone.utc),
    )
    db.add(content_item)
    await db.flush()

    draft.approval_status = "published"
    draft.chosen_variant_index = best_idx
    draft.content_item_id = content_item.id
    rec.consumed = True

    run.content_item_id = content_item.id
    run.threads_media_id = media_id
    run.published_url = f"https://www.threads.net/post/{media_id}"
    run.status = "success"
    run.finished_at = datetime.now(timezone.utc)
    await db.commit()

    await log_action(db, "autopilot_reply", {"media_id": media_id, "target": target.threads_media_id, "text": text[:200]})


async def _check_forbidden(text: str, user_settings) -> bool:
    """Check if text contains any forbidden themes."""
    if not user_settings.forbidden_themes:
        return False
    text_lower = text.lower()
    for theme in user_settings.forbidden_themes:
        if theme.lower() in text_lower:
            return True
    return False


async def _skip(db: AsyncSession, run: AgentRun, reason: str) -> AgentRun:
    """Mark run as skipped with reason."""
    run.decision = "skip" if run.decision == "skip" else run.decision
    run.status = "skipped"
    run.decision_reason = reason
    run.finished_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("Autopilot skipped: %s", reason)
    return run
