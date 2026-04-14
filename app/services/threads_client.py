"""Thin wrapper around the official Threads API.

All API calls go through this module. Handles rate limiting, error handling,
and the 2-step publishing flow.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

THREADS_API_BASE = "https://graph.threads.net/v1.0"
THREADS_OAUTH_BASE = "https://graph.threads.net/oauth"


class ThreadsAPIError(Exception):
    def __init__(self, status_code: int, message: str, raw: dict | None = None):
        self.status_code = status_code
        self.message = message
        self.raw = raw
        super().__init__(f"Threads API {status_code}: {message}")


class ThreadsClient:
    def __init__(self, access_token: str):
        self.access_token = access_token
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self._client.aclose()

    def _params(self, **kwargs) -> dict:
        return {"access_token": self.access_token, **kwargs}

    async def _get(self, url: str, **params) -> dict:
        resp = await self._client.get(url, params=self._params(**params))
        data = resp.json()
        if resp.status_code != 200:
            raise ThreadsAPIError(resp.status_code, data.get("error", {}).get("message", str(data)), data)
        return data

    async def _post(self, url: str, **params) -> dict:
        resp = await self._client.post(url, params=self._params(**params))
        data = resp.json()
        if resp.status_code != 200:
            raise ThreadsAPIError(resp.status_code, data.get("error", {}).get("message", str(data)), data)
        return data

    # --- User Profile ---

    async def get_user_profile(self, user_id: str = "me") -> dict:
        return await self._get(
            f"{THREADS_API_BASE}/{user_id}",
            fields="id,username,threads_profile_picture_url,threads_biography"
        )

    # --- Publishing (2-step flow) ---

    async def create_text_container(self, user_id: str, text: str, reply_to_id: str | None = None) -> str:
        """Step 1: Create a media container. Returns creation_id."""
        params = {"media_type": "TEXT", "text": text}
        if reply_to_id:
            params["reply_to_id"] = reply_to_id
        data = await self._post(f"{THREADS_API_BASE}/{user_id}/threads", **params)
        return data["id"]

    async def wait_for_container(self, creation_id: str, max_wait: int = 30, poll_interval: float = 2.0) -> str:
        """Step 2: Poll container status until FINISHED or ERROR."""
        elapsed = 0.0
        while elapsed < max_wait:
            data = await self._get(
                f"{THREADS_API_BASE}/{creation_id}",
                fields="status,error_message"
            )
            status = data.get("status", "")
            if status == "FINISHED":
                return status
            if status == "ERROR":
                raise ThreadsAPIError(400, f"Container error: {data.get('error_message', 'unknown')}", data)
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        raise ThreadsAPIError(408, f"Container {creation_id} did not finish within {max_wait}s")

    async def publish_container(self, user_id: str, creation_id: str) -> str:
        """Step 3: Publish the container. Returns media_id."""
        data = await self._post(
            f"{THREADS_API_BASE}/{user_id}/threads_publish",
            creation_id=creation_id
        )
        return data["id"]

    async def publish_text_post(self, user_id: str, text: str) -> str:
        """Full 2-step publish: create container → wait → publish. Returns media_id."""
        creation_id = await self.create_text_container(user_id, text)
        await self.wait_for_container(creation_id)
        return await self.publish_container(user_id, creation_id)

    async def publish_reply(self, user_id: str, text: str, reply_to_id: str) -> str:
        """Full 2-step reply publish. Returns media_id."""
        creation_id = await self.create_text_container(user_id, text, reply_to_id=reply_to_id)
        await self.wait_for_container(creation_id)
        return await self.publish_container(user_id, creation_id)

    # --- Read Own Content ---

    async def get_user_threads(self, user_id: str = "me", limit: int = 25) -> list[dict]:
        data = await self._get(
            f"{THREADS_API_BASE}/{user_id}/threads",
            fields="id,text,timestamp,permalink,media_type,is_reply",
            limit=str(limit)
        )
        return data.get("data", [])

    async def get_thread_replies(self, media_id: str) -> list[dict]:
        data = await self._get(
            f"{THREADS_API_BASE}/{media_id}/replies",
            fields="id,text,timestamp,username,permalink"
        )
        return data.get("data", [])

    # --- Insights ---

    async def get_media_insights(self, media_id: str) -> dict:
        data = await self._get(
            f"{THREADS_API_BASE}/{media_id}/insights",
            metric="views,likes,replies,reposts,quotes,shares"
        )
        result = {}
        for item in data.get("data", []):
            name = item.get("name", "")
            values = item.get("values", [{}])
            result[name] = values[0].get("value", 0) if values else 0
        return result

    # --- Keyword Search ---

    async def keyword_search(self, query: str, user_id: str = "me", limit: int = 10) -> list[dict]:
        """Search public Threads posts by keyword. Requires threads_keyword_search scope."""
        data = await self._get(
            f"{THREADS_API_BASE}/{user_id}/threads_search",
            q=query,
            fields="id,text,timestamp,permalink,username",
            limit=str(limit)
        )
        return data.get("data", [])

    # --- Mentions ---

    async def get_mentions(self, user_id: str = "me", limit: int = 25) -> list[dict]:
        """Get posts where user was mentioned. Requires threads_manage_mentions scope."""
        data = await self._get(
            f"{THREADS_API_BASE}/{user_id}/threads/mentions",
            fields="id,text,timestamp,permalink,username",
            limit=str(limit)
        )
        return data.get("data", [])


# --- OAuth Helpers (module-level, no client needed) ---

async def exchange_code_for_token(app_id: str, app_secret: str, redirect_uri: str, code: str) -> dict:
    """Exchange authorization code for short-lived token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{THREADS_OAUTH_BASE}/access_token",
            data={
                "client_id": app_id,
                "client_secret": app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code,
            }
        )
        data = resp.json()
        if resp.status_code != 200:
            raise ThreadsAPIError(resp.status_code, str(data), data)
        return data  # {"access_token": "...", "user_id": "..."}


async def exchange_for_long_lived_token(app_secret: str, short_lived_token: str) -> dict:
    """Exchange short-lived token for long-lived token (60 days)."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{THREADS_API_BASE}/access_token",
            params={
                "grant_type": "th_exchange_token",
                "client_secret": app_secret,
                "access_token": short_lived_token,
            }
        )
        data = resp.json()
        if resp.status_code != 200:
            raise ThreadsAPIError(resp.status_code, str(data), data)
        return data  # {"access_token": "...", "token_type": "bearer", "expires_in": 5184000}


async def refresh_long_lived_token(token: str) -> dict:
    """Refresh a long-lived token. Must be done before expiry."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{THREADS_API_BASE}/refresh_access_token",
            params={
                "grant_type": "th_refresh_token",
                "access_token": token,
            }
        )
        data = resp.json()
        if resp.status_code != 200:
            raise ThreadsAPIError(resp.status_code, str(data), data)
        return data  # {"access_token": "...", "token_type": "bearer", "expires_in": 5184000}
