"""Analytics and recommendation generation."""
from __future__ import annotations

import logging

from openai import AsyncOpenAI
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    ContentItem, MetricsSnapshot, ImportedTarget,
    Recommendation, UserSettings, Topic,
)

logger = logging.getLogger(__name__)

RECOMMENDATION_SYSTEM = """You are a Threads growth strategist. Based on the user's profile, recent performance data, and discovered conversations, generate actionable recommendations.

Rules:
- Be specific, not generic
- Reference actual topics and data when available
- Suggest concrete post angles, not vague themes
- For reply opportunities, explain WHY the conversation is worth joining
- Keep each recommendation under 200 characters for the title, details in body"""


async def generate_recommendations(db: AsyncSession) -> int:
    """Generate daily recommendations based on performance + discovery data."""
    user_settings = (await db.execute(select(UserSettings).limit(1))).scalar_one_or_none()

    # Gather context
    recent_content = (await db.execute(
        select(ContentItem)
        .where(ContentItem.status == "published")
        .order_by(ContentItem.created_at.desc())
        .limit(10)
    )).scalars().all()

    # Get top performing content
    top_metrics = (await db.execute(
        select(MetricsSnapshot)
        .order_by(MetricsSnapshot.views.desc())
        .limit(5)
    )).scalars().all()

    # Get recent discovery targets
    recent_targets = (await db.execute(
        select(ImportedTarget)
        .where(ImportedTarget.source_type.in_(["keyword_search", "manual"]))
        .order_by(ImportedTarget.created_at.desc())
        .limit(10)
    )).scalars().all()

    # Build context for LLM
    context_parts = []
    if user_settings:
        context_parts.append(f"User positioning: {user_settings.positioning or 'not set'}")
        context_parts.append(f"Themes: {', '.join(user_settings.themes) if user_settings.themes else 'not set'}")
        context_parts.append(f"Style: {user_settings.writing_style or 'not set'}")

    if recent_content:
        context_parts.append("\nRecent posts:")
        for item in recent_content[:5]:
            context_parts.append(f"- [{item.item_type}] {item.body_text[:100] if item.body_text else 'no text'}...")

    if top_metrics:
        context_parts.append("\nTop performing content (by views):")
        for m in top_metrics:
            context_parts.append(f"- Content #{m.content_item_id}: {m.views} views, {m.likes} likes, {m.replies} replies")

    if recent_targets:
        context_parts.append("\nRecent discovered conversations:")
        for t in recent_targets[:5]:
            context_parts.append(f"- {t.body_text_snapshot[:100] if t.body_text_snapshot else t.target_url or 'no text'}...")

    prompt = "\n".join(context_parts)
    prompt += """

Based on this data, generate:
1. Exactly 3 post ideas (type: post_idea)
2. Up to 5 reply opportunities (type: reply_opportunity) — only if there are discovered conversations

For each, provide:
- title (short, actionable)
- body (1-2 sentences of detail)
- reason (why this is a good idea)
- score (0.0-1.0, higher = more recommended)

Format each as:
TYPE: post_idea or reply_opportunity
TITLE: ...
BODY: ...
REASON: ...
SCORE: ...
---"""

    # Mark old recommendations as consumed
    old_recs = (await db.execute(
        select(Recommendation).where(Recommendation.consumed == False)
    )).scalars().all()
    for rec in old_recs:
        rec.consumed = True

    client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    try:
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": RECOMMENDATION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1500,
            temperature=0.7,
        )
        raw = response.choices[0].message.content or ""
        recs = _parse_recommendations(raw)
    except Exception as e:
        logger.error("Recommendation generation failed: %s", e)
        recs = []
    finally:
        await client.close()

    for rec_data in recs:
        rec = Recommendation(
            rec_type=rec_data.get("type", "post_idea"),
            title=rec_data.get("title", "Untitled"),
            body=rec_data.get("body"),
            reason=rec_data.get("reason"),
            score=rec_data.get("score", 0.5),
            consumed=False,
        )
        db.add(rec)

    await db.commit()
    return len(recs)


def _parse_recommendations(text: str) -> list[dict]:
    """Parse LLM recommendation output."""
    blocks = [b.strip() for b in text.split("---") if b.strip()]
    recs = []
    for block in blocks:
        rec = {}
        for line in block.strip().split("\n"):
            line = line.strip()
            if line.upper().startswith("TYPE:"):
                rec["type"] = line.split(":", 1)[1].strip().lower()
            elif line.upper().startswith("TITLE:"):
                rec["title"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("BODY:"):
                rec["body"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("REASON:"):
                rec["reason"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("SCORE:"):
                try:
                    rec["score"] = float(line.split(":", 1)[1].strip())
                except ValueError:
                    rec["score"] = 0.5
        if rec.get("title"):
            recs.append(rec)
    return recs
