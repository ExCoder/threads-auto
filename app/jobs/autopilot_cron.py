"""Autopilot agent cron job entry point.

Usage: python -m app.jobs.autopilot_cron
"""
import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()

from app.db import async_session, engine
from app.services.autopilot_service import run_autopilot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    logger.info("Autopilot agent run starting...")
    async with async_session() as db:
        result = await run_autopilot(db)
        logger.info("Autopilot run completed: decision=%s status=%s", result.decision, result.status)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
