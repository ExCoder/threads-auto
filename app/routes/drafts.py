from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Draft, ImportedTarget, ContentItem
from app.services.drafting_service import generate_post_drafts, generate_reply_drafts
from app.services.token_manager import get_active_token
from app.services.threads_client import ThreadsClient, ThreadsAPIError
from app.services.safety_service import (
    check_duplicate, check_daily_post_limit, check_daily_reply_limit,
    check_reply_cooldown, log_action,
)

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# --- Post Drafts ---

@router.get("/posts", response_class=HTMLResponse)
async def list_post_drafts(request: Request, db: AsyncSession = Depends(get_db)):
    drafts = (await db.execute(
        select(Draft).where(Draft.draft_type == "post").order_by(Draft.created_at.desc())
    )).scalars().all()
    return templates.TemplateResponse("drafts_posts.html", {"request": request, "drafts": drafts})


@router.get("/posts/new", response_class=HTMLResponse)
async def new_post_draft_form(request: Request):
    return templates.TemplateResponse("drafts_posts_new.html", {"request": request, "draft": None})


@router.post("/posts/generate")
async def generate_post(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    prompt = form.get("prompt", "").strip()
    if not prompt:
        return RedirectResponse("/drafts/posts/new", status_code=303)

    await generate_post_drafts(db, prompt)
    return RedirectResponse("/drafts/posts", status_code=303)


@router.post("/posts/{draft_id}/publish")
async def publish_post_draft(draft_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    draft = (await db.execute(select(Draft).where(Draft.id == draft_id))).scalar_one_or_none()
    if not draft or not draft.variants:
        return RedirectResponse("/drafts/posts", status_code=303)

    form = await request.form()
    chosen = int(form.get("chosen_variant", 0))
    text = draft.variants[chosen] if chosen < len(draft.variants) else draft.variants[0]
    draft.chosen_variant_index = chosen

    # Safety checks
    limit_reached, count = await check_daily_post_limit(db)
    if limit_reached:
        await log_action(db, "publish_post_blocked", {"reason": "daily_limit", "count": count}, "error")
        draft.approval_status = "rejected"
        await db.commit()
        return RedirectResponse("/drafts/posts", status_code=303)

    is_dup = await check_duplicate(db, text)
    if is_dup:
        await log_action(db, "publish_post_blocked", {"reason": "duplicate"}, "error")
        draft.approval_status = "rejected"
        await db.commit()
        return RedirectResponse("/drafts/posts", status_code=303)

    # Publish via Threads API
    token = await get_active_token(db)
    if not token:
        await log_action(db, "publish_post_failed", {"reason": "no_token"}, "error")
        draft.approval_status = "rejected"
        await db.commit()
        return RedirectResponse("/drafts/posts", status_code=303)

    try:
        client = ThreadsClient(token.access_token)
        media_id = await client.publish_text_post(token.threads_user_id or "me", text)
        await client.close()

        # Create content item
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
        draft.content_item_id = content_item.id
        await db.commit()

        await log_action(db, "publish_post", {"media_id": media_id, "draft_id": draft_id})
    except ThreadsAPIError as e:
        logger.error("Publish failed: %s", e)
        draft.approval_status = "pending"  # Keep as pending so user can retry
        await db.commit()
        await log_action(db, "publish_post_failed", {"error": e.message}, "error", e.message)

    return RedirectResponse("/drafts/posts", status_code=303)


# --- Reply Drafts ---

@router.get("/replies", response_class=HTMLResponse)
async def list_reply_drafts(request: Request, db: AsyncSession = Depends(get_db)):
    drafts = (await db.execute(
        select(Draft).where(Draft.draft_type == "reply").order_by(Draft.created_at.desc())
    )).scalars().all()
    return templates.TemplateResponse("drafts_replies.html", {"request": request, "drafts": drafts})


@router.get("/replies/new", response_class=HTMLResponse)
async def new_reply_draft_form(request: Request, db: AsyncSession = Depends(get_db)):
    targets = (await db.execute(
        select(ImportedTarget).order_by(ImportedTarget.created_at.desc()).limit(20)
    )).scalars().all()
    return templates.TemplateResponse("drafts_replies_new.html", {"request": request, "targets": targets, "draft": None})


@router.post("/replies/generate")
async def generate_reply(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    target_id = form.get("target_id")
    prompt = form.get("prompt", "").strip()
    if not prompt:
        return RedirectResponse("/drafts/replies/new", status_code=303)

    await generate_reply_drafts(db, prompt, int(target_id) if target_id else None)
    return RedirectResponse("/drafts/replies", status_code=303)


@router.post("/replies/{draft_id}/publish")
async def publish_reply_draft(draft_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    draft = (await db.execute(select(Draft).where(Draft.id == draft_id))).scalar_one_or_none()
    if not draft or not draft.variants:
        return RedirectResponse("/drafts/replies", status_code=303)

    form = await request.form()
    chosen = int(form.get("chosen_variant", 0))
    text = draft.variants[chosen] if chosen < len(draft.variants) else draft.variants[0]
    draft.chosen_variant_index = chosen

    # Get the target post ID for reply
    reply_to_id = None
    if draft.imported_target_id:
        target = (await db.execute(
            select(ImportedTarget).where(ImportedTarget.id == draft.imported_target_id)
        )).scalar_one_or_none()
        if target:
            reply_to_id = target.threads_media_id

    if not reply_to_id:
        await log_action(db, "publish_reply_failed", {"reason": "no_target_media_id"}, "error")
        draft.approval_status = "rejected"
        await db.commit()
        return RedirectResponse("/drafts/replies", status_code=303)

    # Safety checks
    limit_reached, count = await check_daily_reply_limit(db)
    if limit_reached:
        await log_action(db, "publish_reply_blocked", {"reason": "daily_limit", "count": count}, "error")
        draft.approval_status = "rejected"
        await db.commit()
        return RedirectResponse("/drafts/replies", status_code=303)

    on_cooldown = await check_reply_cooldown(db, reply_to_id)
    if on_cooldown:
        await log_action(db, "publish_reply_blocked", {"reason": "cooldown", "target": reply_to_id}, "error")
        draft.approval_status = "rejected"
        await db.commit()
        return RedirectResponse("/drafts/replies", status_code=303)

    is_dup = await check_duplicate(db, text)
    if is_dup:
        await log_action(db, "publish_reply_blocked", {"reason": "duplicate"}, "error")
        draft.approval_status = "rejected"
        await db.commit()
        return RedirectResponse("/drafts/replies", status_code=303)

    # Publish via Threads API
    token = await get_active_token(db)
    if not token:
        await log_action(db, "publish_reply_failed", {"reason": "no_token"}, "error")
        draft.approval_status = "rejected"
        await db.commit()
        return RedirectResponse("/drafts/replies", status_code=303)

    try:
        client = ThreadsClient(token.access_token)
        media_id = await client.publish_reply(token.threads_user_id or "me", text, reply_to_id)
        await client.close()

        content_item = ContentItem(
            threads_media_id=media_id,
            item_type="reply",
            body_text=text,
            target_post_id=reply_to_id,
            status="published",
            published_at=datetime.now(timezone.utc),
        )
        db.add(content_item)
        await db.flush()

        draft.approval_status = "published"
        draft.content_item_id = content_item.id
        await db.commit()

        await log_action(db, "publish_reply", {"media_id": media_id, "reply_to": reply_to_id, "draft_id": draft_id})
    except ThreadsAPIError as e:
        logger.error("Reply publish failed: %s", e)
        draft.approval_status = "pending"
        await db.commit()
        await log_action(db, "publish_reply_failed", {"error": e.message}, "error", e.message)

    return RedirectResponse("/drafts/replies", status_code=303)
