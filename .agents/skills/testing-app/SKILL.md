# VideoFactory Local Testing

## Starting the API Server

```bash
PYTHONPATH=src VF_CONFIG=config/settings.example.yaml python -m uvicorn api.server:app --host 127.0.0.1 --port 9000
```

The server starts on port 9000 by default. Environment variables `VF_API_HOST` and `VF_API_PORT` can override.

## Running Unit Tests

```bash
PYTHONPATH=src VF_CONFIG=config/settings.example.yaml python -m pytest -q --ignore=tests/e2e/ --tb=short
```

Expected: ~257 tests pass. 2 pre-existing failures in `test_task_store_sync_and_timeline.py` are known issues on `main`.

## Key Endpoints to Verify

- `GET /api/health` — Returns `{"status": "healthy", "service": "video-factory", "worker": {...}}`. Always returns 200 if API is running.
- `GET /api/system/runtime` — Returns worker heartbeat info + queue stats (`queued`, `active`, `failed`, `total`).
- `GET /` — Dashboard UI with service status sidebar.

## Configuration

- `config/settings.yaml` is gitignored (contains real API keys)
- `config/settings.example.yaml` is safe for CI/testing (placeholder values)
- Set `VF_CONFIG=config/settings.example.yaml` when running without real credentials

## Notes

- The API server does NOT start a background Worker process. Worker is a separate process (`workers/main.py`).
- Health endpoint does not require Worker to be running — it reports worker status as diagnostic info only.
- Dashboard service status hardcodes API as healthy if the page loads; it doesn't parse health endpoint response keys.
