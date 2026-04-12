# Threads Posting Copilot MVP

AI copilot for growing on Threads through quality posts and replies. Uses the official Threads API only.

## Features

- Post draft generation (3 AI-powered variants per request)
- Reply draft generation with conversation context
- Publish posts and replies via official Threads API
- Content sync and metrics tracking
- Keyword-based discovery (requires Meta App Review)
- Daily recommendation engine
- Safety: duplicate prevention, cooldown logic, daily volume limits
- Audit log for all actions

## Quick Start (Local)

```bash
# 1. Clone and enter directory
cd threads-evolve

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env with your keys

# 5. Initialize database
python -m scripts.init_db

# 6. Start the app
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 and login with the password from `ADMIN_PASSWORD` in `.env`.

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `DATABASE_URL` | Database connection string | Yes |
| `THREADS_APP_ID` | Meta Threads App ID | Yes |
| `THREADS_APP_SECRET` | Meta Threads App Secret | Yes |
| `THREADS_REDIRECT_URI` | OAuth callback URL | Yes |
| `LLM_API_KEY` | OpenAI API key | Yes |
| `LLM_MODEL` | LLM model name (default: gpt-4o-mini) | No |
| `SECRET_KEY` | App secret for sessions | Yes |
| `ADMIN_PASSWORD` | Dashboard login password | Yes |

## Railway Deployment

1. Create a Railway project
2. Add PostgreSQL plugin
3. Connect your GitHub repo
4. Set environment variables (use `postgresql+asyncpg://...` for DATABASE_URL)
5. Deploy
6. Run `python -m scripts.init_db` via Railway shell
7. Add cron job: `python -m app.jobs.daily_sync` with schedule `0 6 * * *`

## Architecture

Single-service Python monolith:
- **FastAPI** backend with server-rendered Jinja templates
- **PostgreSQL** (production) / **SQLite** (local dev)
- **OpenAI-compatible LLM** for draft generation and recommendations
- **Official Threads API** for publishing and data sync

## Daily Sync

Runs daily via cron (or manual trigger from dashboard):
1. Refresh OAuth token if needed
2. Sync own published content
3. Sync metrics for recent posts
4. Run keyword discovery (if App Review approved)
5. Generate daily recommendations
