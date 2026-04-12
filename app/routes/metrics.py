from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import MetricsSnapshot, ContentItem

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def metrics_page(request: Request, db: AsyncSession = Depends(get_db)):
    snapshots = (await db.execute(
        select(MetricsSnapshot).order_by(MetricsSnapshot.captured_at.desc()).limit(50)
    )).scalars().all()
    return templates.TemplateResponse("metrics.html", {"request": request, "snapshots": snapshots})
