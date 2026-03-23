import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """隔离任务存储到临时HOME，避免污染本机数据。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("VF_DB_PATH", str(tmp_path / "video_factory.db"))
    monkeypatch.setenv("VF_DISABLE_TITLE_RESOLVE", "1")
    monkeypatch.setenv("VF_USERS_FILE", str(tmp_path / "users.json"))

    from api.routes import distribute as distribute_routes
    from api.routes import publish as publish_routes
    from api.routes import tasks as tasks_routes
    from core.config import Config

    tasks_routes._task_store = None
    distribute_routes._task_store = None
    distribute_routes._scheduler = None
    publish_routes._db = None
    Config.reset()
    yield
    tasks_routes._task_store = None
    distribute_routes._task_store = None
    distribute_routes._scheduler = None
    publish_routes._db = None
    Config.reset()


@pytest.fixture
def app():
    from api.server import app as fastapi_app

    return fastapi_app


@pytest.fixture
def client(app):
    """Authenticated test client — registers a test user so pages are accessible."""
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        # Register a test user so auth is enabled and pages don't redirect
        test_client.post(
            "/api/auth/register",
            json={"username": "testuser", "password": "testpass123"},
        )
        yield test_client


@pytest.fixture
def anon_client(app):
    """Unauthenticated test client — no user registered, no session cookie."""
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client
