# VideoFactory Testing & Development

## Environment Setup

- Python 3.12+
- FFmpeg 4.4+
- SQLite (default) or PostgreSQL

## Install Dependencies

```bash
pip install -r requirements.txt
```

## Configuration

- Config file: `config/settings.yaml` (gitignored)
- Example: `config/settings.example.yaml`
- If `settings.yaml` is missing, it auto-copies from `settings.example.yaml` on startup
- API keys are configured via settings.yaml or environment variables (e.g. `LLM_API_KEY`)

## Start Dev Server

```bash
# From repo root
python -m uvicorn api.server:app --host 0.0.0.0 --port 9000 --reload
```

Server runs at http://localhost:9000. API docs at http://localhost:9000/docs.

## Database

- SQLite DB at `data/video_factory.db` (auto-created on first run)
- Tables are auto-created by `Database.__init__()`
- PRAGMA foreign_keys is enabled at connection init

## Run Tests

```bash
# All tests (excluding e2e)
python -m pytest -q --ignore=tests/e2e/ --tb=short

# Platform/OAuth tests only (Sprint 1)
python -m pytest tests/test_platform_*.py -v

# Full suite expects ~210+ passed, with some pre-existing failures in:
#   - tests/test_task_store.py (2 failures - sync issue)
#   - tests/web/test_preview_and_timeline.py (2 failures)
```

## Authentication

- Bootstrap mode: if no users exist, auth is skipped entirely
- First visit to `/register` creates admin account
- After first user exists, all API/page routes require login
- Session cookies: httpOnly, samesite=lax

## Key Pages for Testing

| Page | URL | What to verify |
|------|-----|----------------|
| Dashboard | `/` | Stats cards, active tasks, service status |
| Platform Accounts | `/platform-accounts` | OAuth platform grid, bound accounts list |
| Settings | `/settings` | API key saving, key masking |
| Publish | `/publish` | Publish queue, job events |
| Storage | `/storage` | R2/local storage stats |

## Key API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|----------|
| `/api/health` | GET | Health check (worker status) |
| `/api/oauth/platforms` | GET | List registered OAuth platforms |
| `/api/oauth/accounts` | GET | List bound platform accounts |
| `/api/oauth/authorize/{platform}` | GET | Start OAuth flow |
| `/api/system/settings/asr-tts` | GET/POST | ASR/TTS settings (masked) |

## Lint & Type Check

```bash
# Lint
flake8 src/ api/ --max-line-length=120

# Type check (if configured)
mypy src/ api/
```

## Docker

```bash
docker build -t videofactory .
docker run -p 9000:9000 -e PORT=9000 videofactory
```

## Railway Deployment Notes

- Binds to `0.0.0.0` on `PORT` env var (Railway injects this)
- Do NOT set Target Port in Railway dashboard — leave empty for auto-detect
- Health check: `/api/health`
- `railway.toml` configures build and deploy settings
