"""LLM-powered draft generation for posts and replies."""
from __future__ import annotations

import logging

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Draft, UserSettings, ImportedTarget
from app.prompts.post_draft import SYSTEM_PROMPT as POST_SYSTEM, build_post_prompt
from app.prompts.reply_draft import SYSTEM_PROMPT as REPLY_SYSTEM, build_reply_prompt

logger = logging.getLogger(__name__)


def _get_llm_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)


async def _get_user_settings(db: AsyncSession) -> UserSettings | None:
    result = await db.execute(select(UserSettings).limit(1))
    return result.scalar_one_or_none()


def _parse_variants(text: str) -> list[str]:
    """Parse LLM response into 3 variants split by ---."""
    parts = [p.strip() for p in text.split("---") if p.strip()]
    # Filter out junk (too short, just dashes, numbering artifacts)
    parts = [p for p in parts if len(p) > 10 and not p.strip("-").strip() == ""]
    # If splitting by --- didn't work, try splitting by double newline
    if len(parts) < 2:
        parts = [p.strip() for p in text.split("\n\n") if p.strip() and len(p.strip()) > 10]
    # Strip leading numbering like "1.", "1)", "Variant 1:"
    import re
    parts = [re.sub(r"^(?:\d+[\.\)]\s*|Variant\s*\d+:?\s*)", "", p).strip() for p in parts]
    parts = [p for p in parts if len(p) > 10]
    # Ensure we have exactly 3
    if len(parts) > 3:
        parts = parts[:3]
    while len(parts) < 3:
        parts.append(parts[-1] if parts else "Draft generation failed")
    return parts


async def generate_post_drafts(db: AsyncSession, topic: str) -> Draft:
    """Generate 3 post draft variants and store them."""
    user_settings = await _get_user_settings(db)

    prompt = build_post_prompt(
        topic=topic,
        positioning=user_settings.positioning if user_settings else "",
        writing_style=user_settings.writing_style if user_settings else "",
        themes=user_settings.themes if user_settings else None,
        forbidden_themes=user_settings.forbidden_themes if user_settings else None,
    )

    client = _get_llm_client()
    try:
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": POST_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1000,
            temperature=0.8,
        )
        raw_text = response.choices[0].message.content or ""
        variants = _parse_variants(raw_text)
    except Exception as e:
        logger.error("LLM post generation failed: %s", e)
        variants = [
            f"[Generation failed: {e}]",
            f"[Try again with a different prompt]",
            f"[Check LLM_API_KEY in settings]",
        ]
    finally:
        await client.close()

    draft = Draft(
        draft_type="post",
        source_prompt=topic,
        variants=variants,
        approval_status="pending",
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return draft


async def generate_reply_drafts(
    db: AsyncSession,
    prompt: str,
    imported_target_id: int | None = None,
) -> Draft:
    """Generate 3 reply draft variants and store them."""
    user_settings = await _get_user_settings(db)

    # Get target text if we have an imported target
    original_text = prompt
    if imported_target_id:
        target = (await db.execute(
            select(ImportedTarget).where(ImportedTarget.id == imported_target_id)
        )).scalar_one_or_none()
        if target and target.body_text_snapshot:
            original_text = target.body_text_snapshot

    reply_prompt = build_reply_prompt(
        original_post_text=original_text,
        context=prompt if imported_target_id else "",
        positioning=user_settings.positioning if user_settings else "",
        writing_style=user_settings.writing_style if user_settings else "",
        themes=user_settings.themes if user_settings else None,
    )

    client = _get_llm_client()
    try:
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": REPLY_SYSTEM},
                {"role": "user", "content": reply_prompt},
            ],
            max_tokens=800,
            temperature=0.8,
        )
        raw_text = response.choices[0].message.content or ""
        variants = _parse_variants(raw_text)
    except Exception as e:
        logger.error("LLM reply generation failed: %s", e)
        variants = [
            f"[Generation failed: {e}]",
            f"[Try again with a different prompt]",
            f"[Check LLM_API_KEY in settings]",
        ]
    finally:
        await client.close()

    draft = Draft(
        draft_type="reply",
        source_prompt=prompt,
        variants=variants,
        imported_target_id=imported_target_id,
        approval_status="pending",
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return draft


async def pick_best_variant(variants: list[str], topic: str, draft_type: str = "post") -> int:
    """Ask LLM to pick the best variant. Returns index 0-2."""
    if len(variants) < 2:
        return 0

    prompt = f"""Pick the single best {draft_type} variant for Threads.

Criteria:
1. Specificity (concrete > vague)
2. Hook strength (first 10 words must grab attention)
3. Brevity (shorter is better if equally good)
4. Authenticity (sounds like a real person, not AI)

Topic: {topic}

Variant 1: {variants[0]}
Variant 2: {variants[1]}
Variant 3: {variants[2] if len(variants) > 2 else variants[0]}

Reply with ONLY the number: 1, 2, or 3"""

    client = _get_llm_client()
    try:
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=5,
            temperature=0.0,
        )
        raw = (response.choices[0].message.content or "").strip()
        num = int(raw[0]) if raw and raw[0] in "123" else 1
        return num - 1  # Convert 1-indexed to 0-indexed
    except Exception as e:
        logger.warning("pick_best_variant failed, defaulting to 0: %s", e)
        return 0
    finally:
        await client.close()
