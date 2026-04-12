from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import ActionLog, SyncLog

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def logs_page(request: Request, db: AsyncSession = Depends(get_db)):
    actions = (await db.execute(
        select(ActionLog).order_by(ActionLog.created_at.desc()).limit(50)
    )).scalars().all()
    syncs = (await db.execute(
        select(SyncLog).order_by(SyncLog.started_at.desc()).limit(20)
    )).scalars().all()
    return templates.TemplateResponse("logs.html", {"request": request, "actions": actions, "syncs": syncs})
