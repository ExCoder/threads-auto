from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_type: Mapped[str] = mapped_column(String(50), nullable=False)  # post / reply
    source_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    variants: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # list of 3 text options
    chosen_variant_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approval_status: Mapped[str] = mapped_column(String(50), default="pending")  # pending / approved / rejected / published
    content_item_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("content_items.id"), nullable=True)
    imported_target_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("imported_targets.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
