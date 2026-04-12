from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import UserSettings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def settings_form(request: Request, db: AsyncSession = Depends(get_db)):
    s = (await db.execute(select(UserSettings).limit(1))).scalar_one_or_none()
    return templates.TemplateResponse("settings.html", {"request": request, "s": s})


@router.post("")
async def save_settings(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    s = (await db.execute(select(UserSettings).limit(1))).scalar_one_or_none()

    themes_raw = form.get("themes", "")
    themes = [t.strip() for t in themes_raw.split(",") if t.strip()]
    forbidden_raw = form.get("forbidden_themes", "")
    forbidden = [t.strip() for t in forbidden_raw.split(",") if t.strip()]

    if s is None:
        s = UserSettings(
            positioning=form.get("positioning", ""),
            themes=themes,
            desired_audience=form.get("desired_audience", ""),
            writing_style=form.get("writing_style", ""),
            forbidden_themes=forbidden,
            daily_post_target=int(form.get("daily_post_target", 3)),
            daily_reply_target=int(form.get("daily_reply_target", 10)),
            growth_goal=form.get("growth_goal", ""),
        )
        db.add(s)
    else:
        s.positioning = form.get("positioning", "")
        s.themes = themes
        s.desired_audience = form.get("desired_audience", "")
        s.writing_style = form.get("writing_style", "")
        s.forbidden_themes = forbidden
        s.daily_post_target = int(form.get("daily_post_target", 3))
        s.daily_reply_target = int(form.get("daily_reply_target", 10))
        s.growth_goal = form.get("growth_goal", "")

    await db.commit()
    return RedirectResponse("/settings", status_code=303)
