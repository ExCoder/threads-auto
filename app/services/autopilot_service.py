"""Autonomous agent: separate post and reply loops.

Posts run every 3 hours. Replies run every 1 hour.
Reply loop is fully autonomous: discovers targets → ranks → replies.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    AgentRun, ContentItem, Draft, ImportedTarget,
    Recommendation, UserSettings, OAuthToken,
)
from app.services.drafting_service import generate_post_drafts, generate_reply_drafts, pick_best_variant
from app.services.discovery_service import auto_discover_targets
from app.services.safety_service import (
    check_duplicate, check_reply_cooldown,
    check_daily_post_limit, check_daily_reply_limit, log_action,
)
from app.services.token_manager import get_active_token, check_and_refresh_token
from app.services.threads_client import ThreadsClient, ThreadsAPIError

logger = logging.getLogger(__name__)

MIN_SCORE = settings.autopilot_min_score


# ──────────────────────────────────────────────
# Public entry points
# ──────────────────────────────────────────────

async def run_autopilot_post(db: AsyncSession) -> AgentRun:
    """Run autonomous post cycle. Called every 3 hours."""
    run = AgentRun(run_type="post", decision="skip", status="running")
    db.add(run)
    await db.commit()
    await db.refresh(run)

    try:
        preflight = await _preflight(db, run)
        if not preflight:
            return run

        token, user_settings = preflight

        post_limit_reached, post_count = await check_daily_post_limit(db)
        run.posts_today = post_count
        if post_limit_reached:
            return await _skip(db, run, "post_daily_limit_reached")

        # Generate recommendations if none exist
        from app.services.analytics_service import generate_recommendations
        unconsumed = (await db.execute(
            select(Recommendation).where(
                Recommendation.rec_type == "post_idea",
                Recommendation.consumed == False,
            ).limit(1)
        )).scalar_one_or_none()
        if not unconsumed:
            logger.info("No post recommendations, generating fresh ones...")
            await generate_recommendations(db)

        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        best_rec = (await db.execute(
            select(Recommendation).where(
                Recommendation.rec_type == "post_idea",
                Recommendation.consumed == False,
                Recommendation.created_at > cutoff,
                Recommendation.score >= MIN_SCORE,
            ).order_by(Recommendation.score.desc()).limit(1)
        )).scalar_one_or_none()

        if not best_rec:
            return await _skip(db, run, "no_post_recommendations")

        run.decision = "post"
        run.recommendation_id = best_rec.id
        run.decision_reason = f"post: \"{best_rec.title[:60]}\" score={best_rec.score:.2f}"

        client = ThreadsClient(token.access_token)
        try:
            await _do_post(db, run, best_rec, client, token.threads_user_id or "me", user_settings)
        finally:
            await client.close()

        return run

    except Exception as e:
        logger.error("Autopilot post error: %s", e, exc_info=True)
        run.status = "error"
        run.error_message = str(e)[:500]
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        return run


async def run_autopilot_reply(db: AsyncSession) -> AgentRun:
    """Run autonomous reply cycle. Called every 1 hour.

    Fully autonomous: discovers targets → ranks via LLM → replies.
    No manual import needed.
    """
    run = AgentRun(run_type="reply", decision="skip", status="running")
    db.add(run)
    await db.commit()
    await db.refresh(run)

    try:
        preflight = await _preflight(db, run)
        if not preflight:
            return run

        token, user_settings = preflight

        reply_limit_reached, reply_count = await check_daily_reply_limit(db)
        run.replies_today = reply_count
        if reply_limit_reached:
            return await _skip(db, run, "reply_daily_limit_reached")

        client = ThreadsClient(token.access_token)
        user_id = token.threads_user_id or "me"

        try:
            # ── AUTO-DISCOVERY ──
            # Find fresh posts to reply to (keyword search + own reply threads)
            discovered = await auto_discover_targets(db, client, user_id)
            run.decision_reason = f"discovered {discovered} new targets"

            # ── FIND CANDIDATES ──
            # Get unreplied targets with media_id, fresh, not failed
            already_replied = select(ContentItem.target_post_id).where(
                ContentItem.target_post_id.isnot(None)
            ).scalar_subquery()

            cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
            candidates = (await db.execute(
                select(ImportedTarget).where(
                    ImportedTarget.threads_media_id.isnot(None),
                    ImportedTarget.relevance_score > 0,
                    ImportedTarget.body_text_snapshot.isnot(None),
                    ImportedTarget.body_text_snapshot != "",
                    ~ImportedTarget.threads_media_id.in_(already_replied),
                    ImportedTarget.created_at > cutoff,
                ).order_by(
                    ImportedTarget.relevance_score.desc(),
                    ImportedTarget.created_at.desc(),
                ).limit(5)
            )).scalars().all()

            if not candidates:
                return await _skip(db, run, f"no_reply_targets (discovered {discovered})")

            # ── LLM RANKING ──
            # Ask LLM which conversation is best to join
            best_target = await _rank_targets(candidates, user_settings)

            # Cooldown check
            if await check_reply_cooldown(db, best_target.threads_media_id):
                return await _skip(db, run, "reply_cooldown")

            run.decision = "reply"
            run.imported_target_id = best_target.id
            run.decision_reason = f"reply to @{_extract_username(best_target)}: \"{(best_target.body_text_snapshot or '')[:50]}\""

            # ── GENERATE + PUBLISH ──
            await _do_reply(db, run, best_target, client, user_id, user_settings)

        finally:
            await client.close()

        return run

    except Exception as e:
        logger.error("Autopilot reply error: %s", e, exc_info=True)
        run.status = "error"
        run.error_message = str(e)[:500]
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        return run


# Backward compat
async def run_autopilot(db: AsyncSession) -> AgentRun:
    return await run_autopilot_post(db)


# ──────────────────────────────────────────────
# LLM target ranking
# ──────────────────────────────────────────────

async def _rank_targets(candidates: list[ImportedTarget], user_settings: UserSettings) -> ImportedTarget:
    """Ask LLM to pick the best conversation to join from top candidates."""
    if len(candidates) == 1:
        return candidates[0]

    descriptions = []
    for i, t in enumerate(candidates, 1):
        source = t.source_type
        username = _extract_username(t)
        text = (t.body_text_snapshot or "")[:200]
        descriptions.append(f"{i}. [@{username}] ({source}) \"{text}\"")

    prompt = f"""You are helping a {user_settings.positioning or 'tech professional'} grow on Threads.
Their themes: {', '.join(user_settings.themes or ['tech'])}.

Pick the ONE conversation where replying would get the most visibility and engagement.
Prefer:
- Posts with many existing replies (active discussion)
- Posts from accounts with large followings
- Topics where the user can add genuine expertise
- Fresh conversations (< 24h old)
- Replies to OUR posts (source: own_reply) are highest priority — respond to your audience first

Candidates:
{chr(10).join(descriptions)}

Reply with ONLY the number (1-{len(candidates)})."""

    client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    try:
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=5,
            temperature=0.0,
        )
        raw = (response.choices[0].message.content or "").strip()
        num = int(raw[0]) if raw and raw[0].isdigit() else 1
        idx = max(0, min(num - 1, len(candidates) - 1))
        logger.info("LLM ranked target #%d as best: %s", idx + 1, candidates[idx].body_text_snapshot[:50] if candidates[idx].body_text_snapshot else "")
        return candidates[idx]
    except Exception as e:
        logger.warning("LLM ranking failed, using first candidate: %s", e)
        return candidates[0]
    finally:
        await client.close()


def _extract_username(target: ImportedTarget) -> str:
    """Extract username from target URL or return 'unknown'."""
    url = target.target_url or ""
    if "/@" in url:
        parts = url.split("/@")
        if len(parts) > 1:
            return parts[1].split("/")[0]
    return "unknown"


# ──────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────

async def _preflight(db: AsyncSession, run: AgentRun) -> tuple[OAuthToken, UserSettings] | None:
    """Common pre-flight checks."""
    now = datetime.now(timezone.utc)

    user_settings = (await db.execute(select(UserSettings).limit(1))).scalar_one_or_none()
    if not user_settings or not user_settings.autopilot_enabled:
        await _skip(db, run, "autopilot_disabled")
        return None

    if not user_settings.positioning or not user_settings.themes:
        await _skip(db, run, "settings_incomplete")
        return None

    token_result = await check_and_refresh_token(db)
    if token_result["status"] in ("no_token", "expired"):
        await _skip(db, run, f"token_{token_result['status']}")
        return None

    token = await get_active_token(db)
    if not token:
        await _skip(db, run, "no_token")
        return None

    # Overlap check (same run_type only)
    recent_running = (await db.execute(
        select(AgentRun).where(
            AgentRun.id != run.id,
            AgentRun.run_type == run.run_type,
            AgentRun.status == "running",
            AgentRun.started_at > now - timedelta(minutes=10),
        )
    )).scalar_one_or_none()
    if recent_running:
        await _skip(db, run, "concurrent_run")
        return None

    return token, user_settings


async def _do_post(db, run, rec, client, user_id, user_settings):
    """Generate, pick, check, publish a post."""
    draft = await generate_post_drafts(db, rec.title)
    run.draft_id = draft.id

    best_idx = await pick_best_variant(draft.variants, rec.title, "post")
    text = draft.variants[best_idx]
    run.chosen_variant_index = best_idx
    run.chosen_variant_text = text

    if await _check_forbidden(text, user_settings):
        return await _skip(db, run, "forbidden_theme_detected")
    if await check_duplicate(db, text):
        return await _skip(db, run, "duplicate")

    try:
        media_id = await client.publish_text_post(user_id, text)
    except ThreadsAPIError as e:
        run.status = "error"
        run.error_message = e.message[:500]
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        await log_action(db, "autopilot_post_failed", {"error": e.message}, "error", e.message)
        return

    content_item = ContentItem(
        threads_media_id=media_id, item_type="post", body_text=text,
        status="published", published_at=datetime.now(timezone.utc),
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


async def _do_reply(db, run, target, client, user_id, user_settings):
    """Generate, pick, check, publish a reply."""
    context = target.body_text_snapshot or target.target_url or "Reply to this conversation"

    draft = await generate_reply_drafts(db, context, target.id)
    run.draft_id = draft.id

    best_idx = await pick_best_variant(draft.variants, context[:200], "reply")
    text = draft.variants[best_idx]
    run.chosen_variant_index = best_idx
    run.chosen_variant_text = text

    if await _check_forbidden(text, user_settings):
        return await _skip(db, run, "forbidden_theme_detected")
    if await check_duplicate(db, text):
        return await _skip(db, run, "duplicate")

    try:
        media_id = await client.publish_reply(user_id, text, target.threads_media_id)
    except ThreadsAPIError as e:
        run.status = "error"
        run.error_message = e.message[:500]
        run.finished_at = datetime.now(timezone.utc)
        target.relevance_score = 0.0
        await db.commit()
        await log_action(db, "autopilot_reply_failed", {"error": e.message, "target": target.threads_media_id}, "error", e.message)
        return

    content_item = ContentItem(
        threads_media_id=media_id, item_type="reply", body_text=text,
        target_post_id=target.threads_media_id,
        status="published", published_at=datetime.now(timezone.utc),
    )
    db.add(content_item)
    await db.flush()

    draft.approval_status = "published"
    draft.chosen_variant_index = best_idx
    draft.content_item_id = content_item.id

    run.content_item_id = content_item.id
    run.threads_media_id = media_id
    run.published_url = f"https://www.threads.net/post/{media_id}"
    run.status = "success"
    run.finished_at = datetime.now(timezone.utc)
    await db.commit()
    await log_action(db, "autopilot_reply", {"media_id": media_id, "target": target.threads_media_id, "text": text[:200]})


async def _check_forbidden(text: str, user_settings) -> bool:
    if not user_settings.forbidden_themes:
        return False
    text_lower = text.lower()
    return any(theme.lower() in text_lower for theme in user_settings.forbidden_themes)


async def _skip(db: AsyncSession, run: AgentRun, reason: str) -> AgentRun:
    run.status = "skipped"
    if not run.decision_reason:
        run.decision_reason = reason
    else:
        run.decision_reason += f" → {reason}"
    run.finished_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("Autopilot %s skipped: %s", run.run_type, reason)
    return run
