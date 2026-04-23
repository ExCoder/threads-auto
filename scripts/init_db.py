"""Initialize the database by creating all tables.

Usage: python -m scripts.init_db           # create missing tables (SAFE, keeps data)
       python -m scripts.init_db --reset   # DROP and recreate all tables (WIPES DATA)
"""
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from app.db import Base, engine
from app.models import *  # noqa: F401, F403


async def init():
    reset = "--reset" in sys.argv or os.environ.get("DB_RESET", "").lower() in ("1", "true", "yes")

    async with engine.begin() as conn:
        if reset:
            print("⚠️  DB_RESET=true → dropping ALL tables (all data will be lost)")
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
            print("Database tables recreated (DATA WIPED).")
        else:
            # Safe mode: only create tables that don't exist yet. Preserves data.
            await conn.run_sync(Base.metadata.create_all)
            print("Database tables ensured (existing data preserved).")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init())
