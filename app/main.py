from __future__ import annotations

import logging
import secrets

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.routes import auth, autopilot, content, dashboard, drafts, logs, metrics, settings_page, sync, targets

logging.basicConfig(level=getattr(logging, settings.log_level))
logger = logging.getLogger(__name__)

app = FastAPI(title="Threads Posting Copilot", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# --- Simple password auth middleware ---

AUTH_COOKIE = "tc_auth"


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # Allow healthcheck, static files, and login without auth
    path = request.url.path
    if path in ("/health", "/auth/login") or path.startswith("/static"):
        return await call_next(request)

    # Check cookie
    token = request.cookies.get(AUTH_COOKIE)
    if token == _auth_token():
        return await call_next(request)

    # Show login form for GET, reject for POST
    if request.method == "GET":
        return templates.TemplateResponse("login.html", {"request": request, "error": None})

    return RedirectResponse("/auth/login", status_code=303)


def _auth_token() -> str:
    """Derive a stable auth token from the admin password + secret key."""
    raw = f"{settings.admin_password}:{settings.secret_key}"
    return secrets.token_hex(16) if not settings.admin_password else __import__("hashlib").sha256(raw.encode()).hexdigest()[:32]


# --- Routes ---

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(dashboard.router, tags=["dashboard"])
app.include_router(settings_page.router, prefix="/settings", tags=["settings"])
app.include_router(drafts.router, prefix="/drafts", tags=["drafts"])
app.include_router(targets.router, prefix="/targets", tags=["targets"])
app.include_router(content.router, prefix="/content", tags=["content"])
app.include_router(metrics.router, prefix="/metrics", tags=["metrics"])
app.include_router(logs.router, prefix="/logs", tags=["logs"])
app.include_router(sync.router, prefix="/sync", tags=["sync"])
app.include_router(autopilot.router, prefix="/autopilot", tags=["autopilot"])


# --- Startup seeding ---

@app.on_event("startup")
async def seed_defaults():
    """Populate default UserSettings on first boot."""
    from app.db import async_session
    from scripts.seed import seed_user_settings
    try:
        async with async_session() as db:
            await seed_user_settings(db)
    except Exception as e:
        logger.error("Seed failed (non-fatal): %s", e)


# --- Autopilot scheduler (posts every 3h, replies every 1h) ---

@app.on_event("startup")
async def start_autopilot_scheduler():
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    async def _post_job():
        from app.db import async_session
        from app.services.autopilot_service import run_autopilot_post
        try:
            async with async_session() as db:
                result = await run_autopilot_post(db)
                logger.info("Autopilot post: decision=%s status=%s", result.decision, result.status)
        except Exception as e:
            logger.error("Autopilot post error: %s", e)

    async def _reply_job():
        from app.db import async_session
        from app.services.autopilot_service import run_autopilot_reply
        try:
            async with async_session() as db:
                result = await run_autopilot_reply(db)
                logger.info("Autopilot reply: decision=%s status=%s", result.decision, result.status)
        except Exception as e:
            logger.error("Autopilot reply error: %s", e)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _post_job,
        IntervalTrigger(hours=settings.autopilot_post_interval_hours),
        id="autopilot_post",
        replace_existing=True,
    )
    scheduler.add_job(
        _reply_job,
        IntervalTrigger(hours=settings.autopilot_reply_interval_hours),
        id="autopilot_reply",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Autopilot scheduler: posts every %dh, replies every %dh",
                settings.autopilot_post_interval_hours, settings.autopilot_reply_interval_hours)


@app.get("/health")
async def healthcheck():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def root():
    return RedirectResponse("/dashboard")
