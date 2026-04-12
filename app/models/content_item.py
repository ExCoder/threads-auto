from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ContentItem(Base):
    __tablename__ = "content_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    threads_media_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_type: Mapped[str] = mapped_column(String(50), nullable=False)  # post / reply
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic_tags: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # list of strings
    target_post_id: Mapped[str | None] = mapped_column(String(100), nullable=True)  # for replies
    status: Mapped[str] = mapped_column(String(50), default="published")  # published / failed
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
