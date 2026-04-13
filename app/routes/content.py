from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import ContentItem

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def list_content(request: Request, db: AsyncSession = Depends(get_db)):
    items = (await db.execute(
        select(ContentItem)
        .where(ContentItem.body_text.isnot(None), ContentItem.body_text != "")
        .order_by(ContentItem.created_at.desc())
    )).scalars().all()
    return templates.TemplateResponse("content.html", {"request": request, "items": items})
