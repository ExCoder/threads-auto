from __future__ import annotations

import hashlib
import logging
import urllib.parse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.services.token_manager import store_token_from_code, get_active_token
from app.services.threads_client import ThreadsAPIError

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

AUTH_COOKIE = "tc_auth"


def _auth_token() -> str:
    raw = f"{settings.admin_password}:{settings.secret_key}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login(request: Request):
    form = await request.form()
    password = form.get("password", "")
    if password == settings.admin_password:
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie(AUTH_COOKIE, _auth_token(), httponly=True, max_age=86400 * 7)
        return response
    return templates.TemplateResponse("login.html", {"request": request, "error": "Wrong password"})


@router.get("/logout")
async def logout():
    response = RedirectResponse("/auth/login", status_code=303)
    response.delete_cookie(AUTH_COOKIE)
    return response


# --- Threads OAuth ---

THREADS_AUTH_URL = "https://threads.net/oauth/authorize"
THREADS_SCOPES = "threads_basic,threads_content_publish,threads_manage_replies,threads_read_replies"


@router.get("/threads/connect")
async def threads_connect():
    """Redirect user to Threads OAuth authorization page."""
    params = urllib.parse.urlencode({
        "client_id": settings.threads_app_id,
        "redirect_uri": settings.threads_redirect_uri,
        "scope": THREADS_SCOPES,
        "response_type": "code",
        "state": "threads_copilot",
    })
    return RedirectResponse(f"{THREADS_AUTH_URL}?{params}")


@router.get("/callback")
async def threads_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle OAuth callback from Threads."""
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        logger.error("OAuth error: %s - %s", error, request.query_params.get("error_description"))
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": f"Threads OAuth error: {error}"
        })

    if not code:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "No authorization code received"
        })

    try:
        token = await store_token_from_code(
            db=db,
            app_id=settings.threads_app_id,
            app_secret=settings.threads_app_secret,
            redirect_uri=settings.threads_redirect_uri,
            code=code,
        )
        logger.info("Threads account connected: user_id=%s", token.threads_user_id)
        return RedirectResponse("/dashboard", status_code=303)
    except ThreadsAPIError as e:
        logger.error("OAuth token exchange failed: status=%s message=%s raw=%s", e.status_code, e.message, e.raw)
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": f"Token exchange failed: {e.message}"
        })
    except Exception as e:
        logger.error("OAuth unexpected error: %s", e, exc_info=True)
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": f"Unexpected error: {e}"
        })
