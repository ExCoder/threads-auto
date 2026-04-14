from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class KnownAccount(Base):
    __tablename__ = "known_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    interaction_count: Mapped[int] = mapped_column(Integer, default=1)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[str] = mapped_column(String(50), default="reply")  # reply / mention / conversation
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
