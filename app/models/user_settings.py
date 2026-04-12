from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UserSettings(Base):
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    positioning: Mapped[str | None] = mapped_column(Text, nullable=True)
    themes: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # list of strings
    desired_audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    writing_style: Mapped[str | None] = mapped_column(Text, nullable=True)
    forbidden_themes: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # list of strings
    daily_post_target: Mapped[int] = mapped_column(Integer, default=3)
    daily_reply_target: Mapped[int] = mapped_column(Integer, default=10)
    growth_goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    autopilot_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
