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
        await conn.run_sync(Base.metadata.create_all)
    print("Database tables created successfully.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init())
