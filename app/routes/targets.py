from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import ImportedTarget

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def list_targets(request: Request, db: AsyncSession = Depends(get_db)):
    targets = (await db.execute(
        select(ImportedTarget).order_by(ImportedTarget.created_at.desc())
    )).scalars().all()
    return templates.TemplateResponse("targets.html", {"request": request, "targets": targets})


@router.get("/new", response_class=HTMLResponse)
async def import_form(request: Request):
    return templates.TemplateResponse("targets_new.html", {"request": request})


@router.post("/import")
async def import_target(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    url = form.get("target_url", "").strip()
    text = form.get("body_text", "").strip()

    target = ImportedTarget(
        target_url=url or None,
        body_text_snapshot=text or None,
        source_type="manual",
        import_method="manual_paste" if text else "api",
    )
    # TODO: Stage 6 — if URL provided, try to resolve threads_media_id via API
    db.add(target)
    await db.commit()
    return RedirectResponse("/targets", status_code=303)
