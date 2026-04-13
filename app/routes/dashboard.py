from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Recommendation, SyncLog, ContentItem, OAuthToken

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    # Post ideas
    post_ideas = (await db.execute(
        select(Recommendation)
        .where(Recommendation.rec_type == "post_idea", Recommendation.consumed == False)
        .order_by(Recommendation.score.desc())
        .limit(3)
    )).scalars().all()

    # Reply opportunities
    reply_opps = (await db.execute(
        select(Recommendation)
        .where(Recommendation.rec_type == "reply_opportunity", Recommendation.consumed == False)
        .order_by(Recommendation.score.desc())
        .limit(5)
    )).scalars().all()

    # Recent published (exclude media-only posts with no text)
    recent_content = (await db.execute(
        select(ContentItem)
        .where(ContentItem.body_text.isnot(None), ContentItem.body_text != "")
        .order_by(ContentItem.created_at.desc())
        .limit(5)
    )).scalars().all()

    # Last sync
    last_sync = (await db.execute(
        select(SyncLog).order_by(SyncLog.started_at.desc()).limit(1)
    )).scalar_one_or_none()

    # Token health
    token = (await db.execute(
        select(OAuthToken).order_by(OAuthToken.id.desc()).limit(1)
    )).scalar_one_or_none()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "post_ideas": post_ideas,
        "reply_opps": reply_opps,
        "recent_content": recent_content,
        "last_sync": last_sync,
        "token": token,
    })
