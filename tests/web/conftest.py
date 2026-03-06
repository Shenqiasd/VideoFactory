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

    from api.routes import tasks as tasks_routes
    from core.config import Config

    tasks_routes._task_store = None
    Config.reset()
    yield
    tasks_routes._task_store = None
    Config.reset()


@pytest.fixture
def app():
    from api.server import app as fastapi_app

    return fastapi_app


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client
