from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import ImportedTarget, OAuthToken
from app.services.threads_client import ThreadsClient, ThreadsAPIError

logger = logging.getLogger(__name__)
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


def _extract_threads_post_id(url: str) -> str | None:
    """Try to extract a Threads post ID from a URL.

    Threads URLs look like:
    https://www.threads.net/@username/post/ABC123XYZ
    https://threads.net/@username/post/ABC123XYZ
    https://www.threads.com/@username/post/ABC123XYZ
    """
    match = re.search(r"threads\.(?:net|com)/@[\w.]+/post/([\w-]+)", url)
    return match.group(1) if match else None


async def _resolve_media_id(db: AsyncSession, url: str, shortcode: str) -> str | None:
    """Try to resolve a Threads post URL to a media_id via keyword search.

    Since Threads API doesn't have a direct URL-to-ID endpoint,
    we search for the post content via the API.
    Returns media_id if found, None otherwise.
    """
    token = (await db.execute(
        select(OAuthToken).order_by(OAuthToken.id.desc()).limit(1)
    )).scalar_one_or_none()
    if not token:
        return None

    # Try to search for the post using its shortcode
    client = ThreadsClient(token.access_token)
    try:
        results = await client.keyword_search(shortcode, limit=1)
        for item in results:
            permalink = item.get("permalink", "")
            if shortcode in permalink:
                return item.get("id")
    except ThreadsAPIError as e:
        logger.warning("Could not resolve media_id for %s: %s", url, e.message)
    finally:
        await client.close()

    return None


@router.post("/import")
async def import_target(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    url = form.get("target_url", "").strip()
    text = form.get("body_text", "").strip()

    threads_media_id = None
    import_method = "manual_paste"

    # If URL provided, try to resolve media_id
    if url:
        shortcode = _extract_threads_post_id(url)
        if shortcode:
            threads_media_id = await _resolve_media_id(db, url, shortcode)
            if threads_media_id:
                import_method = "api"
                logger.info("Resolved media_id %s for URL %s", threads_media_id, url)
            else:
                logger.info("Could not resolve media_id for URL %s (shortcode=%s)", url, shortcode)

    target = ImportedTarget(
        target_url=url or None,
        threads_media_id=threads_media_id,
        body_text_snapshot=text or None,
        source_type="manual",
        import_method=import_method,
    )
    db.add(target)
    await db.commit()
    return RedirectResponse("/targets", status_code=303)
