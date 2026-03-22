# Testing VideoFactory

## Quick Start

```bash
cd /home/ubuntu/repos/VideoFactory
PYTHONPATH=src python scripts/start_server.py
```

Server starts at `http://localhost:9000` by default (or `$PORT` if set).

## Unit Tests

```bash
PYTHONPATH=src python -m pytest -q --ignore=tests/e2e/ --tb=short
```

- ~210+ tests, runs in ~4 seconds
- 2 known pre-existing failures in `test_task_store_sync_and_timeline.py` (unrelated to most changes)
- `tests/e2e/` requires Playwright browser and running server — skip for quick validation

## Key Architecture Notes

- **Config loading**: `core.config.Config()` handles missing `config/settings.yaml` gracefully (returns empty config). The settings page GET endpoints use `Config()` directly.
- **Settings save**: POST endpoints use `_read_yaml_config()` in `api/routes/system.py` and `api/routes/storage.py`, which calls `_ensure_config_file()` to auto-create `settings.yaml` from `settings.example.yaml` if missing.
- **Config file location**: `config/settings.yaml` is in `.gitignore` and `.dockerignore` — it never exists in fresh Docker containers or CI. The auto-init mechanism handles this.
- **Settings page**: Navigate to `http://localhost:9000/settings` — has tabs for Translation, Storage, Publish, Notification, System config.

## Testing Settings Page

1. To simulate Docker/Railway environment: `rm config/settings.yaml` before starting server
2. Navigate to `/settings` — page should load with defaults from `settings.example.yaml`
3. Change a value (e.g., Whisper timeout), click save button at bottom
4. Green toast "ASR/TTS 配置已保存" confirms success
5. Navigate away and back to verify persistence
6. Verify file creation: `ls -la config/settings.yaml`

## Testing Storage Cleanup API

```bash
curl -s http://localhost:9000/api/storage/cleanup-config | python3 -m json.tool
```

Should return JSON with `enabled`, `schedule`, and `rules` array (6 default rules).

## Railway Deployment Notes

- Server must bind to `0.0.0.0` (not `127.0.0.1`) for Railway's reverse proxy
- Railway injects `PORT` env var (usually 8080) — app reads it automatically
- Health check endpoint: `GET /api/health` — returns 200 if API is running
- Railway Dashboard > Settings > Networking: Target Port should be left empty (auto-detect)
- `railway.toml` configures health check path and restart policy

## Common Issues

- **"配置文件不存在"**: `settings.yaml` missing in container — the auto-init fix in PR #14 handles this
- **"Application failed to respond" on Railway**: Check host binding (must be `0.0.0.0`), PORT env var, and Target Port setting in Railway Dashboard
- **Tests fail with FileNotFoundError on settings.yaml**: Tests should fall back to `settings.example.yaml` — check `tests/web/test_api_contract.py` for the fallback pattern

## Devin Secrets Needed

No secrets required for basic testing. For full feature testing:
- `GROQ_API_KEY` — for Whisper ASR
- `VOLCENGINE_*` — for TTS and translation
- Platform cookies (Douyin, Bilibili) — for publish testing
