from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Run type and decision
    run_type: Mapped[str] = mapped_column(String(50), default="post")  # post / reply
    decision: Mapped[str] = mapped_column(String(50), nullable=False)  # post / reply / skip
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Input references
    recommendation_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("recommendations.id"), nullable=True)
    imported_target_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("imported_targets.id"), nullable=True)

    # Generated content
    draft_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("drafts.id"), nullable=True)
    chosen_variant_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chosen_variant_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Published result
    content_item_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("content_items.id"), nullable=True)
    threads_media_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    published_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Status
    status: Mapped[str] = mapped_column(String(50), default="running")  # running / success / skipped / error
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Context at run time
    posts_today: Mapped[int] = mapped_column(Integer, default=0)
    replies_today: Mapped[int] = mapped_column(Integer, default=0)
