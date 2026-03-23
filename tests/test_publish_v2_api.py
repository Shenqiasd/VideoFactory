"""
Publish V2 API 路由单元测试。

使用独立 FastAPI app（仅包含 publish_v2 router），
避免 server.py lifespan 覆盖 mock。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from platform_services.publish_queue import PublishStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_queue():
    """Create a mock PublishQueue."""
    q = MagicMock()
    q.enqueue = AsyncMock()
    q.retry_task = AsyncMock(return_value=True)
    q.cancel_task = AsyncMock(return_value=True)
    return q


@pytest.fixture
def mock_db():
    """Create a mock Database with v2 CRUD."""
    db = MagicMock()
    return db


@pytest.fixture
def client(mock_queue, mock_db):
    """
    Create a test client with a standalone app containing only
    the publish_v2 router, with mocked db and queue.
    """
    import api.routes.publish_v2 as pv2_module
    from api.auth import require_auth

    original_db = pv2_module._db
    original_queue = pv2_module._publish_queue

    pv2_module._db = mock_db
    pv2_module._publish_queue = mock_queue

    test_app = FastAPI()
    test_app.include_router(pv2_module.router, prefix="/api/publish/v2")
    # Override require_auth dependency to be a no-op (bypass login)
    test_app.dependency_overrides[require_auth] = lambda: None
    with TestClient(test_app, raise_server_exceptions=False) as tc:
        yield tc

    pv2_module._db = original_db
    pv2_module._publish_queue = original_queue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task_record(task_id=None, status="pending", platform="youtube"):
    return {
        "id": task_id or str(uuid.uuid4()),
        "user_id": "",
        "account_id": str(uuid.uuid4()),
        "platform": platform,
        "title": "Test",
        "description": "",
        "tags": [],
        "video_path": "/tmp/v.mp4",
        "cover_path": "",
        "status": status,
        "scheduled_at": None,
        "attempts": 0,
        "max_attempts": 3,
        "error_message": "",
        "post_id": "",
        "permalink": "",
        "platform_options": {},
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "published_at": None,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCreatePublishTask:
    def test_create_returns_task_ids(self, client, mock_queue):
        """POST /create should return task_ids for each platform target."""
        account_id = str(uuid.uuid4())
        mock_queue.enqueue.return_value = "task-1"

        resp = client.post("/api/publish/v2/create", json={
            "video_path": "/tmp/v.mp4",
            "title": "Test Video",
            "platforms": [
                {"platform": "youtube", "account_id": account_id},
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["data"]["task_ids"]) == 1

    def test_create_multi_platform(self, client, mock_queue):
        """POST /create with multiple platforms should create multiple tasks."""
        mock_queue.enqueue.side_effect = ["task-1", "task-2"]

        resp = client.post("/api/publish/v2/create", json={
            "video_path": "/tmp/v.mp4",
            "title": "Multi Test",
            "platforms": [
                {"platform": "youtube", "account_id": "acc1"},
                {"platform": "bilibili", "account_id": "acc2"},
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]["task_ids"]) == 2
        assert mock_queue.enqueue.call_count == 2


class TestListTasks:
    def test_list_tasks_with_filters(self, client, mock_db):
        """GET /tasks should return filtered task list."""
        task = _task_record(status="published", platform="youtube")
        mock_db.get_publish_tasks_v2 = MagicMock(return_value=[task])
        mock_db.count_publish_tasks_v2 = MagicMock(return_value=1)

        resp = client.get("/api/publish/v2/tasks?status=published&platform=youtube")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["data"]["tasks"]) == 1
        assert data["data"]["total"] == 1

    def test_list_tasks_empty(self, client, mock_db):
        """GET /tasks should return empty list when no tasks."""
        mock_db.get_publish_tasks_v2 = MagicMock(return_value=[])
        mock_db.count_publish_tasks_v2 = MagicMock(return_value=0)
        resp = client.get("/api/publish/v2/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["tasks"] == []
        assert data["data"]["total"] == 0


class TestGetTask:
    def test_get_task_detail(self, client, mock_db):
        """GET /tasks/{id} should return task detail."""
        task = _task_record()
        mock_db.get_publish_task_v2 = MagicMock(return_value=task)

        resp = client.get(f"/api/publish/v2/tasks/{task['id']}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["id"] == task["id"]

    def test_get_task_not_found(self, client, mock_db):
        """GET /tasks/{id} should return 404 for missing task."""
        mock_db.get_publish_task_v2 = MagicMock(return_value=None)
        resp = client.get("/api/publish/v2/tasks/nonexistent")
        assert resp.status_code == 404


class TestRetryTask:
    def test_retry_task_success(self, client, mock_queue):
        """POST /tasks/{id}/retry should succeed for failed tasks."""
        mock_queue.retry_task.return_value = True
        resp = client.post("/api/publish/v2/tasks/task-1/retry")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_retry_task_not_allowed(self, client, mock_queue):
        """POST /tasks/{id}/retry should fail if task is not retryable."""
        mock_queue.retry_task.return_value = False
        resp = client.post("/api/publish/v2/tasks/task-1/retry")
        assert resp.status_code == 400


class TestDeleteTask:
    def test_delete_pending_task(self, client, mock_db, mock_queue):
        """DELETE /tasks/{id} should cancel a pending task."""
        task = _task_record(status="pending")
        mock_db.get_publish_task_v2 = MagicMock(return_value=task)

        resp = client.delete(f"/api/publish/v2/tasks/{task['id']}")
        assert resp.status_code == 200
        mock_queue.cancel_task.assert_called_once()

    def test_delete_failed_task(self, client, mock_db, mock_queue):
        """DELETE /tasks/{id} should delete a failed task."""
        task = _task_record(status="failed")
        mock_db.get_publish_task_v2 = MagicMock(return_value=task)
        mock_db.delete_publish_task_v2 = MagicMock()

        resp = client.delete(f"/api/publish/v2/tasks/{task['id']}")
        assert resp.status_code == 200
        mock_db.delete_publish_task_v2.assert_called_once()

    def test_delete_publishing_task_rejected(self, client, mock_db):
        """DELETE /tasks/{id} should return 409 for in-flight publishing task."""
        task = _task_record(status="publishing")
        mock_db.get_publish_task_v2 = MagicMock(return_value=task)

        resp = client.delete(f"/api/publish/v2/tasks/{task['id']}")
        assert resp.status_code == 409

    def test_delete_nonexistent_task(self, client, mock_db):
        """DELETE /tasks/{id} should return 404 for missing task."""
        mock_db.get_publish_task_v2 = MagicMock(return_value=None)
        resp = client.delete("/api/publish/v2/tasks/nonexistent")
        assert resp.status_code == 404


class TestStats:
    def test_stats_endpoint(self, client, mock_db):
        """GET /stats should return counts by status."""
        mock_db.count_publish_tasks_v2 = MagicMock(return_value=0)
        resp = client.get("/api/publish/v2/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "total" in data["data"]
        assert "pending" in data["data"]
        assert "published" in data["data"]
