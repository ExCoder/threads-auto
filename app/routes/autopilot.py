from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import AgentRun, UserSettings
from app.services.autopilot_service import run_autopilot_post, run_autopilot_reply
from app.services.safety_service import check_daily_post_limit, check_daily_reply_limit

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def autopilot_page(request: Request, db: AsyncSession = Depends(get_db)):
    runs = (await db.execute(
        select(AgentRun).order_by(AgentRun.started_at.desc()).limit(50)
    )).scalars().all()

    user_settings = (await db.execute(select(UserSettings).limit(1))).scalar_one_or_none()
    enabled = user_settings.autopilot_enabled if user_settings else False

    _, post_count = await check_daily_post_limit(db)
    _, reply_count = await check_daily_reply_limit(db)

    return templates.TemplateResponse("autopilot.html", {
        "request": request,
        "runs": runs,
        "enabled": enabled,
        "post_count": post_count,
        "reply_count": reply_count,
        "max_posts": 5,
        "max_replies": 15,
    })


@router.post("/toggle")
async def toggle_autopilot(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    enabled = form.get("enabled") == "on"

    s = (await db.execute(select(UserSettings).limit(1))).scalar_one_or_none()
    if not s:
        s = UserSettings(autopilot_enabled=enabled)
        db.add(s)
    else:
        s.autopilot_enabled = enabled
    await db.commit()

    return RedirectResponse("/autopilot", status_code=303)


@router.post("/trigger/post")
async def trigger_post(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        result = await run_autopilot_post(db)
        logger.info("Manual post run: decision=%s status=%s", result.decision, result.status)
    except Exception as e:
        logger.error("Manual post run failed: %s", e)
    return RedirectResponse("/autopilot", status_code=303)


@router.post("/trigger/reply")
async def trigger_reply(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        result = await run_autopilot_reply(db)
        logger.info("Manual reply run: decision=%s status=%s", result.decision, result.status)
    except Exception as e:
        logger.error("Manual reply run failed: %s", e)
    return RedirectResponse("/autopilot", status_code=303)


# Keep old trigger for backward compat
@router.post("/trigger")
async def trigger_any(request: Request, db: AsyncSession = Depends(get_db)):
    return await trigger_post(request, db)
