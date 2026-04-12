"""Discovery service: keyword search + import management."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImportedTarget, Topic, UserSettings
from app.services.threads_client import ThreadsClient, ThreadsAPIError

logger = logging.getLogger(__name__)


async def run_keyword_discovery(db: AsyncSession, client: ThreadsClient) -> int:
    """Search for relevant public posts based on user topics. Returns count of new items."""
    settings = (await db.execute(select(UserSettings).limit(1))).scalar_one_or_none()
    if not settings or not settings.themes:
        return 0

    topics = settings.themes[:10]  # Max 10 topics
    new_count = 0

    for topic_name in topics:
        try:
            results = await client.keyword_search(topic_name, limit=5)
            for item in results:
                media_id = item.get("id")
                if not media_id:
                    continue

                # Skip if already imported
                existing = (await db.execute(
                    select(ImportedTarget).where(ImportedTarget.threads_media_id == media_id)
                )).scalar_one_or_none()
                if existing:
                    continue

                target = ImportedTarget(
                    target_url=item.get("permalink"),
                    threads_media_id=media_id,
                    body_text_snapshot=item.get("text", "")[:2000],
                    source_type="keyword_search",
                    import_method="api",
                    topic_tags=[topic_name],
                    relevance_score=0.5,
                )
                db.add(target)
                new_count += 1

            # Update topic tracking
            topic = (await db.execute(
                select(Topic).where(Topic.name == topic_name)
            )).scalar_one_or_none()
            if not topic:
                topic = Topic(name=topic_name, source="user", score=0.5)
                db.add(topic)

        except ThreadsAPIError as e:
            if e.status_code == 403:
                logger.warning("Keyword search not available (needs App Review): %s", e.message)
                break  # No point trying other topics
            logger.error("Keyword search failed for '%s': %s", topic_name, e)

    await db.commit()
    return new_count
