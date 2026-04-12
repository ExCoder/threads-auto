"""Daily sync cron job entry point.

Usage: python -m app.jobs.daily_sync
"""
import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()

from app.db import async_session, engine
from app.services.sync_service import run_full_sync

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    logger.info("Starting daily sync...")
    async with async_session() as db:
        results = await run_full_sync(db)
        logger.info("Daily sync completed: %s", results)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
