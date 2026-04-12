from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.services.sync_service import run_full_sync

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/trigger")
async def trigger_sync(db: AsyncSession = Depends(get_db)):
    try:
        results = await run_full_sync(db)
        logger.info("Manual sync completed: %s", results)
    except Exception as e:
        logger.error("Manual sync failed: %s", e)
    return RedirectResponse("/dashboard", status_code=303)
