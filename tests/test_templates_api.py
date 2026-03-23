"""
Templates API + Batch Publish 路由单元测试。

使用独立 FastAPI app（仅包含 templates + publish_v2 router），
避免 server.py lifespan 覆盖 mock。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import Database


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """Create a fresh DB for each test."""
    db_path = str(tmp_path / "test_templates_api.db")
    database = Database(db_path=db_path)
    yield database
    database.close()


@pytest.fixture
def mock_queue():
    """Create a mock PublishQueue."""
    q = MagicMock()
    q.enqueue = AsyncMock()
    q.retry_task = AsyncMock(return_value=True)
    q.cancel_task = AsyncMock(return_value=True)
    return q


@pytest.fixture
def client(db, mock_queue):
    """
    Create a test client with standalone apps containing
    templates + publish_v2 routers, with real db and mocked queue.
    """
    import api.routes.templates as tmpl_module
    import api.routes.publish_v2 as pv2_module
    from api.auth import require_auth

    # Save originals
    orig_tmpl_db = tmpl_module._db
    orig_pv2_db = pv2_module._db
    orig_pv2_queue = pv2_module._publish_queue

    # Inject test dependencies
    tmpl_module._db = db
    pv2_module._db = db
    pv2_module._publish_queue = mock_queue

    test_app = FastAPI()
    test_app.include_router(tmpl_module.router, prefix="/api/templates")
    test_app.include_router(pv2_module.router, prefix="/api/publish/v2")
    test_app.dependency_overrides[require_auth] = lambda: None

    with TestClient(test_app, raise_server_exceptions=False) as tc:
        yield tc

    # Restore
    tmpl_module._db = orig_tmpl_db
    pv2_module._db = orig_pv2_db
    pv2_module._publish_queue = orig_pv2_queue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_template(client, **overrides):
    """Helper to create a template and return the response data."""
    body = {
        "name": "Test Template",
        "platforms": ["youtube", "bilibili"],
        "title_template": "{{video}} - EP {{ep}}",
        "description_template": "Watch {{video}}",
        "tags": ["tag1", "tag2"],
        "platform_options": {"youtube": {"category": "Gaming"}},
        "user_id": "test-user",
    }
    body.update(overrides)
    resp = client.post("/api/templates", json=body)
    return resp


# ---------------------------------------------------------------------------
# Template CRUD API Tests
# ---------------------------------------------------------------------------


class TestListTemplates:
    def test_list_empty(self, client):
        """GET /api/templates returns empty list when no templates."""
        resp = client.get("/api/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"] == []

    def test_list_all(self, client):
        """GET /api/templates returns all templates."""
        _create_template(client, name="T1")
        _create_template(client, name="T2")
        resp = client.get("/api/templates")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2

    def test_list_by_user_id(self, client):
        """GET /api/templates?user_id=X filters by user."""
        _create_template(client, name="Alice T", user_id="alice")
        _create_template(client, name="Bob T", user_id="bob")
        resp = client.get("/api/templates?user_id=alice")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["name"] == "Alice T"


class TestCreateTemplate:
    def test_create_success(self, client):
        """POST /api/templates creates a template."""
        resp = _create_template(client)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "id" in data["data"]
        assert data["data"]["name"] == "Test Template"

    def test_create_empty_name(self, client):
        """POST /api/templates rejects empty name."""
        resp = _create_template(client, name="")
        assert resp.status_code == 400
        assert resp.json()["success"] is False

    def test_create_minimal(self, client):
        """POST /api/templates with minimal fields."""
        resp = client.post("/api/templates", json={"name": "Minimal"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True


class TestGetTemplate:
    def test_get_existing(self, client):
        """GET /api/templates/{id} returns template detail."""
        create_resp = _create_template(client)
        template_id = create_resp.json()["data"]["id"]

        resp = client.get(f"/api/templates/{template_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["id"] == template_id

    def test_get_not_found(self, client):
        """GET /api/templates/{id} returns 404 for missing template."""
        resp = client.get("/api/templates/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["success"] is False


class TestUpdateTemplate:
    def test_update_success(self, client):
        """PUT /api/templates/{id} updates fields."""
        create_resp = _create_template(client)
        template_id = create_resp.json()["data"]["id"]

        resp = client.put(f"/api/templates/{template_id}", json={
            "name": "Updated Name",
            "platforms": ["tiktok"],
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Verify update
        detail = client.get(f"/api/templates/{template_id}").json()["data"]
        assert detail["name"] == "Updated Name"
        assert json.loads(detail["platforms"]) == ["tiktok"]

    def test_update_not_found(self, client):
        """PUT /api/templates/{id} returns 404 for missing template."""
        resp = client.put("/api/templates/nonexistent", json={"name": "X"})
        assert resp.status_code == 404

    def test_update_no_fields(self, client):
        """PUT /api/templates/{id} with empty body returns success (no-op)."""
        create_resp = _create_template(client)
        template_id = create_resp.json()["data"]["id"]
        resp = client.put(f"/api/templates/{template_id}", json={})
        assert resp.status_code == 200
        assert "无需更新" in resp.json()["message"]


class TestDeleteTemplate:
    def test_delete_success(self, client):
        """DELETE /api/templates/{id} removes the template."""
        create_resp = _create_template(client)
        template_id = create_resp.json()["data"]["id"]

        resp = client.delete(f"/api/templates/{template_id}")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Verify deleted
        resp = client.get(f"/api/templates/{template_id}")
        assert resp.status_code == 404

    def test_delete_not_found(self, client):
        """DELETE /api/templates/{id} returns 404 for missing template."""
        resp = client.delete("/api/templates/nonexistent")
        assert resp.status_code == 404


class TestApplyTemplate:
    def test_apply_success(self, client):
        """POST /api/templates/{id}/apply generates task specs."""
        create_resp = _create_template(client)
        template_id = create_resp.json()["data"]["id"]

        resp = client.post(f"/api/templates/{template_id}/apply", json={
            "video_path": "/tmp/video.mp4",
            "title_vars": {"video": "Cool Video", "ep": "3"},
            "desc_vars": {"video": "Cool Video"},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        tasks = data["data"]
        assert len(tasks) == 2  # youtube + bilibili
        assert tasks[0]["title"] == "Cool Video - EP 3"
        assert tasks[0]["description"] == "Watch Cool Video"
        assert tasks[0]["video_path"] == "/tmp/video.mp4"

    def test_apply_not_found(self, client):
        """POST /api/templates/{id}/apply returns 404 for missing template."""
        resp = client.post("/api/templates/nonexistent/apply", json={
            "video_path": "/tmp/v.mp4",
        })
        assert resp.status_code == 404

    def test_apply_empty_platforms(self, client):
        """POST /api/templates/{id}/apply returns 404 when no platforms."""
        create_resp = _create_template(client, platforms=[])
        template_id = create_resp.json()["data"]["id"]

        resp = client.post(f"/api/templates/{template_id}/apply", json={
            "video_path": "/tmp/v.mp4",
        })
        assert resp.status_code == 404
        assert "无平台配置" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Batch Publish API Tests
# ---------------------------------------------------------------------------


class TestBatchPublish:
    def test_batch_create_success(self, client, mock_queue):
        """POST /api/publish/v2/batch creates tasks for all platforms."""
        mock_queue.enqueue.side_effect = [
            "task-1", "task-2", "task-3",
        ]

        resp = client.post("/api/publish/v2/batch", json={
            "tasks": [{
                "video_path": "/tmp/v.mp4",
                "title": "Test",
                "description": "Desc",
                "tags": ["t1"],
                "cover_path": "",
                "platforms": [
                    {"platform": "youtube", "account_id": "acc1"},
                    {"platform": "bilibili", "account_id": "acc2"},
                    {"platform": "tiktok", "account_id": "acc3"},
                ],
            }],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["count"] == 3
        assert len(data["data"]["tasks"]) == 3
        assert mock_queue.enqueue.call_count == 3

    def test_batch_multiple_tasks(self, client, mock_queue):
        """POST /api/publish/v2/batch with multiple task specs."""
        mock_queue.enqueue.side_effect = ["t1", "t2", "t3", "t4"]

        resp = client.post("/api/publish/v2/batch", json={
            "tasks": [
                {
                    "video_path": "/tmp/v1.mp4",
                    "title": "Video 1",
                    "platforms": [
                        {"platform": "youtube", "account_id": "acc1"},
                    ],
                },
                {
                    "video_path": "/tmp/v2.mp4",
                    "title": "Video 2",
                    "platforms": [
                        {"platform": "bilibili", "account_id": "acc2"},
                        {"platform": "tiktok", "account_id": "acc3"},
                        {"platform": "douyin", "account_id": "acc4"},
                    ],
                },
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["count"] == 4

    def test_batch_empty_tasks(self, client, mock_queue):
        """POST /api/publish/v2/batch with empty tasks list."""
        resp = client.post("/api/publish/v2/batch", json={"tasks": []})
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["count"] == 0
        assert data["data"]["tasks"] == []

    def test_batch_queue_unavailable(self, client, mock_queue):
        """POST /api/publish/v2/batch returns 503 when queue is None."""
        import api.routes.publish_v2 as pv2_module
        saved = pv2_module._publish_queue
        pv2_module._publish_queue = None

        resp = client.post("/api/publish/v2/batch", json={
            "tasks": [{
                "video_path": "/tmp/v.mp4",
                "title": "Test",
                "platforms": [{"platform": "youtube", "account_id": "acc1"}],
            }],
        })
        assert resp.status_code == 503

        pv2_module._publish_queue = saved
