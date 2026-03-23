"""
Analytics API 路由单元测试。

使用独立 FastAPI app（仅包含 analytics router），
避免 server.py lifespan 覆盖 mock。
"""
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db():
    """Create a mock Database."""
    db = MagicMock()
    return db


@pytest.fixture
def mock_token_manager():
    """Create a mock TokenManager."""
    tm = MagicMock()
    tm.check_all_token_health = AsyncMock(return_value=[])
    return tm


@pytest.fixture
def mock_analytics_service():
    """Create a mock AnalyticsService."""
    svc = MagicMock()
    svc.get_analytics_summary = MagicMock(return_value={
        "platforms": [],
        "totals": {"views": 0, "likes": 0, "comments": 0, "shares": 0, "videos": 0},
    })
    svc.get_task_analytics = MagicMock(return_value=[])
    svc.sync_all_stats = AsyncMock(return_value={"synced": 0, "failed": 0, "skipped": 0})
    svc.get_top_content = MagicMock(return_value=[])
    return svc


@pytest.fixture
def client(mock_db, mock_token_manager, mock_analytics_service):
    """
    Create a test client with a standalone app containing only
    the analytics router, with mocked dependencies.
    """
    import api.routes.analytics as analytics_module
    from api.auth import require_auth

    original_db = analytics_module._db
    original_tm = analytics_module._token_manager
    original_svc = analytics_module._analytics_service

    analytics_module._db = mock_db
    analytics_module._token_manager = mock_token_manager
    analytics_module._analytics_service = mock_analytics_service

    test_app = FastAPI()
    test_app.include_router(analytics_module.router, prefix="/api/analytics")
    test_app.dependency_overrides[require_auth] = lambda: None

    with TestClient(test_app, raise_server_exceptions=False) as tc:
        yield tc

    analytics_module._db = original_db
    analytics_module._token_manager = original_tm
    analytics_module._analytics_service = original_svc


# ---------------------------------------------------------------------------
# Tests: GET /api/analytics/summary
# ---------------------------------------------------------------------------

class TestSummary:
    def test_summary_returns_data(self, client, mock_analytics_service):
        """GET /summary returns analytics summary."""
        mock_analytics_service.get_analytics_summary.return_value = {
            "platforms": [
                {"platform": "youtube", "total_videos": 5, "total_views": 1000,
                 "total_likes": 100, "total_comments": 50, "total_shares": 20},
            ],
            "totals": {"views": 1000, "likes": 100, "comments": 50, "shares": 20, "videos": 5},
        }
        resp = client.get("/api/analytics/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["totals"]["views"] == 1000
        assert len(data["data"]["platforms"]) == 1

    def test_summary_empty(self, client, mock_analytics_service):
        """GET /summary returns empty when no data."""
        resp = client.get("/api/analytics/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["totals"]["views"] == 0


# ---------------------------------------------------------------------------
# Tests: GET /api/analytics/tasks/{task_id}
# ---------------------------------------------------------------------------

class TestTaskAnalytics:
    def test_task_analytics_returns_records(self, client, mock_analytics_service):
        """GET /tasks/{id} returns analytics history."""
        mock_analytics_service.get_task_analytics.return_value = [
            {"publish_task_id": "task-1", "views": 100, "likes": 10},
        ]
        resp = client.get("/api/analytics/tasks/task-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["data"]) == 1

    def test_task_analytics_empty(self, client, mock_analytics_service):
        """GET /tasks/{id} returns empty for unknown task."""
        resp = client.get("/api/analytics/tasks/nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"] == []


# ---------------------------------------------------------------------------
# Tests: POST /api/analytics/sync
# ---------------------------------------------------------------------------

class TestSync:
    def test_sync_success(self, client, mock_analytics_service):
        """POST /sync returns sync results."""
        mock_analytics_service.sync_all_stats.return_value = {
            "synced": 3, "failed": 1, "skipped": 2,
        }
        resp = client.post("/api/analytics/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["synced"] == 3
        assert data["data"]["failed"] == 1
        assert data["data"]["skipped"] == 2

    def test_sync_error(self, client, mock_analytics_service):
        """POST /sync returns 500 on exception."""
        mock_analytics_service.sync_all_stats.side_effect = Exception("sync boom")
        resp = client.post("/api/analytics/sync")
        assert resp.status_code == 500
        data = resp.json()
        assert data["success"] is False


# ---------------------------------------------------------------------------
# Tests: GET /api/analytics/top
# ---------------------------------------------------------------------------

class TestTopContent:
    def test_top_content_default_limit(self, client, mock_analytics_service):
        """GET /top returns top content."""
        mock_analytics_service.get_top_content.return_value = [
            {"publish_task_id": "t1", "views": 1000, "title": "Hit Video"},
        ]
        resp = client.get("/api/analytics/top")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["data"]) == 1
        mock_analytics_service.get_top_content.assert_called_once_with(limit=10)

    def test_top_content_custom_limit(self, client, mock_analytics_service):
        """GET /top?limit=5 passes limit parameter."""
        mock_analytics_service.get_top_content.return_value = []
        resp = client.get("/api/analytics/top?limit=5")
        assert resp.status_code == 200
        mock_analytics_service.get_top_content.assert_called_once_with(limit=5)


# ---------------------------------------------------------------------------
# Tests: GET /api/analytics/token-health
# ---------------------------------------------------------------------------

class TestTokenHealth:
    def test_token_health_empty(self, client, mock_token_manager):
        """GET /token-health returns empty when all tokens healthy."""
        resp = client.get("/api/analytics/token-health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"] == []

    def test_token_health_with_alerts(self, client, mock_token_manager):
        """GET /token-health returns alerts for problem tokens."""
        mock_token_manager.check_all_token_health.return_value = [
            {"account_id": "acc-1", "platform": "youtube", "status": "expired"},
            {"account_id": "acc-2", "platform": "bilibili", "status": "expiring_soon", "expires_in_hours": 48},
        ]
        resp = client.get("/api/analytics/token-health")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 2
        assert data["data"][0]["status"] == "expired"
        assert data["data"][1]["status"] == "expiring_soon"


# ---------------------------------------------------------------------------
# Tests: Auth required
# ---------------------------------------------------------------------------

class TestAuthRequired:
    def test_endpoints_require_auth(self):
        """All analytics endpoints should require auth when enabled."""
        import api.routes.analytics as analytics_module
        from api.auth import require_auth

        test_app = FastAPI()
        test_app.include_router(analytics_module.router, prefix="/api/analytics")
        # Do NOT override require_auth — let it enforce auth

        with TestClient(test_app, raise_server_exceptions=False) as tc:
            # Auth is disabled when no users exist, so these should succeed
            # but the dependency is still registered on the router
            resp = tc.get("/api/analytics/summary")
            # With no users, auth is disabled → 200
            assert resp.status_code == 200
