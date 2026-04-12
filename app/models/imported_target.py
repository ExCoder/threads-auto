from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ImportedTarget(Base):
    __tablename__ = "imported_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    threads_media_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    body_text_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(50), default="manual")  # manual / keyword_search / mention / own_reply
    import_method: Mapped[str] = mapped_column(String(50), default="manual_paste")  # api / manual_paste
    topic_tags: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
