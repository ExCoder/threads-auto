"""Initialize the database by creating all tables.

Usage: python -m scripts.init_db
"""
import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from app.db import Base, engine
from app.models import *  # noqa: F401, F403


async def init():
    async with engine.begin() as conn:
        # For MVP: drop and recreate all tables to handle schema changes
        # TODO: switch to alembic migrations for production
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    print("Database tables recreated successfully.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init())
