"""OAuth token lifecycle management.

Handles token storage, refresh, and health checking.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OAuthToken, SyncLog
from app.services.threads_client import (
    exchange_code_for_token,
    exchange_for_long_lived_token,
    refresh_long_lived_token,
    ThreadsAPIError,
)

logger = logging.getLogger(__name__)

REFRESH_THRESHOLD_DAYS = 7  # Refresh if expiring within this many days


async def get_active_token(db: AsyncSession) -> OAuthToken | None:
    result = await db.execute(
        select(OAuthToken).order_by(OAuthToken.id.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def store_token_from_code(
    db: AsyncSession,
    app_id: str,
    app_secret: str,
    redirect_uri: str,
    code: str,
) -> OAuthToken:
    """Complete OAuth flow: code → short-lived → long-lived → store."""
    # Step 1: Exchange code for short-lived token
    short_data = await exchange_code_for_token(app_id, app_secret, redirect_uri, code)
    short_token = short_data["access_token"]
    user_id = str(short_data.get("user_id", ""))

    # Step 2: Exchange for long-lived token
    long_data = await exchange_for_long_lived_token(app_secret, short_token)
    access_token = long_data["access_token"]
    expires_in = long_data.get("expires_in", 5184000)  # 60 days default

    now = datetime.now(timezone.utc)
    token = OAuthToken(
        access_token=access_token,
        token_type=long_data.get("token_type", "bearer"),
        expires_at=now + timedelta(seconds=expires_in),
        last_refresh_at=now,
        refresh_status="ok",
        threads_user_id=user_id,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)
    logger.info("Stored new long-lived token for user %s, expires %s", user_id, token.expires_at)
    return token


async def check_and_refresh_token(db: AsyncSession) -> dict:
    """Check token health and refresh if needed. Returns status dict."""
    token = await get_active_token(db)
    if not token:
        return {"status": "no_token", "message": "No OAuth token found"}

    now = datetime.now(timezone.utc)
    expires_at = token.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if not expires_at:
        return {"status": "unknown", "message": "Token has no expiry date"}

    days_left = (expires_at - now).days

    if days_left < 0:
        token.refresh_status = "expired"
        await db.commit()
        return {"status": "expired", "message": "Token has expired. Re-authenticate."}

    if days_left <= REFRESH_THRESHOLD_DAYS:
        # Try to refresh
        try:
            data = await refresh_long_lived_token(token.access_token)
            token.access_token = data["access_token"]
            token.expires_at = now + timedelta(seconds=data.get("expires_in", 5184000))
            token.last_refresh_at = now
            token.refresh_status = "ok"
            await db.commit()
            logger.info("Token refreshed successfully, new expiry: %s", token.expires_at)
            return {"status": "refreshed", "message": f"Token refreshed. Expires in {(token.expires_at - now).days} days."}
        except ThreadsAPIError as e:
            token.refresh_status = "error"
            await db.commit()
            logger.error("Token refresh failed: %s", e)
            return {"status": "refresh_error", "message": f"Refresh failed: {e.message}"}

    return {"status": "ok", "message": f"Token healthy. {days_left} days until expiry."}


def token_health_summary(token: OAuthToken | None) -> dict:
    """Quick health check without API calls."""
    if not token:
        return {"healthy": False, "status": "disconnected", "days_left": 0}

    now = datetime.now(timezone.utc)
    expires_at = token.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if not expires_at:
        return {"healthy": False, "status": "unknown_expiry", "days_left": 0}

    days_left = (expires_at - now).days
    if days_left < 0:
        return {"healthy": False, "status": "expired", "days_left": days_left}
    if days_left <= REFRESH_THRESHOLD_DAYS:
        return {"healthy": True, "status": "needs_refresh", "days_left": days_left}
    return {"healthy": True, "status": "ok", "days_left": days_left}
