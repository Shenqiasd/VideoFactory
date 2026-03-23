"""
Sprint 3: publish_v2 API 路由单元测试。

使用 FastAPI TestClient 测试发布 V2 API 端点。
"""

import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.routes.publish_v2 import router  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db_mock():
    """创建 mock 数据库。"""
    db = MagicMock()
    db._tasks = {}

    def insert_task(task):
        db._tasks[task["id"]] = dict(task)

    def get_task(task_id):
        return db._tasks.get(task_id)

    def update_task(task_id, **fields):
        if task_id in db._tasks:
            db._tasks[task_id].update(fields)

    def delete_task(task_id):
        db._tasks.pop(task_id, None)

    def get_tasks(*, platform=None, status=None, account_id=None, limit=100):
        results = []
        for t in db._tasks.values():
            if platform and t.get("platform") != platform:
                continue
            if status and t.get("status") != status:
                continue
            if account_id and t.get("account_id") != account_id:
                continue
            results.append(t)
        return results[:limit]

    db.insert_publish_task_v2.side_effect = insert_task
    db.get_publish_task_v2.side_effect = get_task
    db.update_publish_task_v2.side_effect = update_task
    db.delete_publish_task_v2.side_effect = delete_task
    db.get_publish_tasks_v2.side_effect = get_tasks
    return db


def _make_queue_mock():
    """创建 mock 发布队列。"""
    queue = MagicMock()
    queue.enqueue = AsyncMock(side_effect=lambda data: data.get("id", str(uuid.uuid4())))
    queue.retry_task = AsyncMock(return_value=True)
    queue.cancel_task = AsyncMock(return_value=True)
    return queue


def _create_test_app(db=None, queue=None):
    """创建带 mock 状态的测试 FastAPI app。"""
    app = FastAPI()
    app.include_router(router, prefix="/api/publish/v2")

    if db is None:
        db = _make_db_mock()
    if queue is None:
        queue = _make_queue_mock()

    app.state.publish_db = db
    app.state.publish_queue = queue

    return app, db, queue


# Disable auth for tests
@pytest.fixture(autouse=True)
def disable_auth():
    with patch("api.routes.publish_v2.require_auth", return_value=None):
        yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCreatePublishTask:
    def test_create_task_returns_task_ids(self):
        """POST /create 应返回 task_ids。"""
        app, db, queue = _create_test_app()
        client = TestClient(app)

        resp = client.post("/api/publish/v2/create", json={
            "video_path": "/tmp/video.mp4",
            "title": "Test Video",
            "description": "desc",
            "tags": ["tag1"],
            "platforms": [
                {"platform": "youtube", "account_id": "acc_1"},
            ],
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["task_ids"]) == 1
        queue.enqueue.assert_called_once()

    def test_create_multi_platform_task(self):
        """POST /create 多平台应创建多个任务。"""
        app, db, queue = _create_test_app()
        client = TestClient(app)

        resp = client.post("/api/publish/v2/create", json={
            "video_path": "/tmp/video.mp4",
            "title": "Multi Platform Video",
            "platforms": [
                {"platform": "youtube", "account_id": "acc_1"},
                {"platform": "bilibili", "account_id": "acc_2"},
                {"platform": "tiktok", "account_id": "acc_3"},
            ],
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["task_ids"]) == 3
        assert queue.enqueue.call_count == 3

    def test_create_task_with_scheduled_at(self):
        """POST /create 带 scheduled_at 应传递给 enqueue。"""
        app, db, queue = _create_test_app()
        client = TestClient(app)

        resp = client.post("/api/publish/v2/create", json={
            "video_path": "/tmp/video.mp4",
            "title": "Scheduled Video",
            "platforms": [
                {"platform": "youtube", "account_id": "acc_1"},
            ],
            "scheduled_at": "2099-01-01T00:00:00",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        # Check that scheduled_at was passed to enqueue
        call_args = queue.enqueue.call_args[0][0]
        assert call_args["scheduled_at"] == "2099-01-01T00:00:00"


class TestListTasks:
    def test_list_tasks_with_filters(self):
        """GET /tasks 应支持过滤。"""
        db = _make_db_mock()
        # Pre-populate tasks
        db._tasks = {
            "t1": {"id": "t1", "platform": "youtube", "status": "published", "title": "V1", "account_id": "a1"},
            "t2": {"id": "t2", "platform": "bilibili", "status": "pending", "title": "V2", "account_id": "a2"},
            "t3": {"id": "t3", "platform": "youtube", "status": "pending", "title": "V3", "account_id": "a1"},
        }
        app, _, queue = _create_test_app(db=db)
        client = TestClient(app)

        # Filter by platform
        resp = client.get("/api/publish/v2/tasks?platform=youtube")
        data = resp.json()
        assert data["success"] is True
        assert len(data["tasks"]) == 2

        # Filter by status
        resp = client.get("/api/publish/v2/tasks?status=pending")
        data = resp.json()
        assert len(data["tasks"]) == 2

        # Filter by both
        resp = client.get("/api/publish/v2/tasks?platform=youtube&status=pending")
        data = resp.json()
        assert len(data["tasks"]) == 1

    def test_list_tasks_empty(self):
        """GET /tasks 空列表。"""
        app, db, queue = _create_test_app()
        client = TestClient(app)

        resp = client.get("/api/publish/v2/tasks")
        data = resp.json()
        assert data["success"] is True
        assert data["tasks"] == []


class TestGetTaskDetail:
    def test_get_task_detail(self):
        """GET /tasks/{task_id} 应返回任务详情。"""
        db = _make_db_mock()
        db._tasks = {
            "t1": {
                "id": "t1", "platform": "youtube", "status": "published",
                "title": "Detail Video", "account_id": "a1",
                "tags": ["tag1"], "platform_options": {},
            },
        }
        app, _, queue = _create_test_app(db=db)
        client = TestClient(app)

        resp = client.get("/api/publish/v2/tasks/t1")
        data = resp.json()
        assert data["success"] is True
        assert data["task"]["title"] == "Detail Video"

    def test_get_task_detail_not_found(self):
        """GET /tasks/{task_id} 不存在应返回 404。"""
        app, db, queue = _create_test_app()
        client = TestClient(app)

        resp = client.get("/api/publish/v2/tasks/nonexistent")
        assert resp.status_code == 404


class TestRetryTask:
    def test_retry_task(self):
        """POST /tasks/{task_id}/retry 应重试任务。"""
        app, db, queue = _create_test_app()
        client = TestClient(app)

        resp = client.post("/api/publish/v2/tasks/t1/retry")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        queue.retry_task.assert_called_once_with("t1")

    def test_retry_task_fails(self):
        """POST /tasks/{task_id}/retry 失败应返回 400。"""
        queue = _make_queue_mock()
        queue.retry_task = AsyncMock(return_value=False)
        app, db, _ = _create_test_app(queue=queue)
        client = TestClient(app)

        resp = client.post("/api/publish/v2/tasks/t1/retry")
        assert resp.status_code == 400


class TestDeleteTask:
    def test_delete_task(self):
        """DELETE /tasks/{task_id} 应取消任务。"""
        app, db, queue = _create_test_app()
        client = TestClient(app)

        resp = client.delete("/api/publish/v2/tasks/t1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        queue.cancel_task.assert_called_once_with("t1")

    def test_delete_task_fails(self):
        """DELETE /tasks/{task_id} 失败应返回 400。"""
        queue = _make_queue_mock()
        queue.cancel_task = AsyncMock(return_value=False)
        app, db, _ = _create_test_app(queue=queue)
        client = TestClient(app)

        resp = client.delete("/api/publish/v2/tasks/t1")
        assert resp.status_code == 400


class TestStats:
    def test_stats_endpoint(self):
        """GET /stats 应返回按状态分组的统计。"""
        db = _make_db_mock()
        db._tasks = {
            "t1": {"id": "t1", "status": "published"},
            "t2": {"id": "t2", "status": "published"},
            "t3": {"id": "t3", "status": "pending"},
            "t4": {"id": "t4", "status": "failed"},
        }
        app, _, queue = _create_test_app(db=db)
        client = TestClient(app)

        resp = client.get("/api/publish/v2/stats")
        data = resp.json()
        assert data["success"] is True
        assert data["stats"]["published"] == 2
        assert data["stats"]["pending"] == 1
        assert data["stats"]["failed"] == 1
        assert data["stats"]["total"] == 4
