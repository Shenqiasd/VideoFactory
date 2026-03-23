"""
Analytics 服务 + DB CRUD + Token 健康检查单元测试。
"""
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from core.database import Database
from platform_services.analytics import AnalyticsService
from platform_services.token_manager import TokenManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    """Create a fresh in-memory-like DB for each test."""
    db_path = str(tmp_path / "test.db")
    return Database(db_path=db_path)


@pytest.fixture
def mock_registry():
    """Mock PlatformRegistry."""
    registry = MagicMock()
    return registry


@pytest.fixture
def mock_token_manager():
    """Mock TokenManager."""
    tm = MagicMock()
    tm.get_valid_token = AsyncMock()
    return tm


@pytest.fixture
def analytics_service(db, mock_token_manager, mock_registry):
    """AnalyticsService with real DB and mocked token/registry."""
    return AnalyticsService(db=db, token_manager=mock_token_manager, registry=mock_registry)


def _insert_platform_account(db, account_id="acc-1", platform="youtube"):
    """Helper to insert a platform account."""
    db.insert_platform_account({
        "id": account_id,
        "platform": platform,
        "platform_uid": account_id,
        "nickname": "Test User",
    })


def _insert_published_task(db, task_id=None, account_id="acc-1", platform="youtube", post_id="post-123"):
    """Helper to insert a published v2 task."""
    tid = task_id or str(uuid.uuid4())
    _insert_platform_account(db, account_id, platform)
    db.insert_publish_task_v2({
        "id": tid,
        "account_id": account_id,
        "platform": platform,
        "title": "Test Video",
        "status": "published",
        "post_id": post_id,
    })
    return tid


# ---------------------------------------------------------------------------
# DB CRUD Tests
# ---------------------------------------------------------------------------

def _seed_task(db, task_id, platform="youtube", account_id="acc-1"):
    """Helper: ensure platform_account + publish_tasks_v2 row exist."""
    # platform_account may already exist — ignore duplicate
    try:
        _insert_platform_account(db, account_id, platform)
    except Exception:
        pass
    db.insert_publish_task_v2({
        "id": task_id,
        "account_id": account_id,
        "platform": platform,
        "title": f"Video {task_id}",
        "status": "published",
        "post_id": f"post-{task_id}",
    })


class TestContentAnalyticsDB:
    def test_upsert_and_get(self, db):
        """upsert_content_analytics + get_content_analytics round-trip."""
        task_id = "task-1"
        _seed_task(db, task_id)
        db.upsert_content_analytics(
            publish_task_id=task_id,
            platform="youtube",
            post_id="post-1",
            views=100,
            likes=10,
            comments=5,
            shares=2,
            raw_data={"extra": "data"},
        )
        records = db.get_content_analytics(task_id)
        assert len(records) == 1
        assert records[0]["views"] == 100
        assert records[0]["likes"] == 10
        assert records[0]["comments"] == 5
        assert records[0]["shares"] == 2
        assert records[0]["raw_data"]["extra"] == "data"

    def test_multiple_records_for_same_task(self, db):
        """Multiple analytics records can exist for the same task."""
        task_id = "task-1"
        _seed_task(db, task_id)
        for i in range(3):
            db.upsert_content_analytics(
                publish_task_id=task_id,
                platform="youtube",
                post_id="post-1",
                views=100 * (i + 1),
                likes=10 * (i + 1),
            )
        records = db.get_content_analytics(task_id)
        assert len(records) == 3

    def test_get_analytics_summary(self, db):
        """get_analytics_summary aggregates by platform."""
        _seed_task(db, "task-1", "youtube")
        _seed_task(db, "task-2", "bilibili")
        db.upsert_content_analytics(
            publish_task_id="task-1",
            platform="youtube",
            post_id="post-1",
            views=1000,
            likes=100,
            comments=50,
            shares=20,
        )
        db.upsert_content_analytics(
            publish_task_id="task-2",
            platform="bilibili",
            post_id="post-2",
            views=500,
            likes=50,
            comments=25,
            shares=10,
        )
        summary = db.get_analytics_summary()
        assert "platforms" in summary
        assert "totals" in summary
        assert summary["totals"]["views"] == 1500
        assert summary["totals"]["likes"] == 150
        assert len(summary["platforms"]) == 2

    def test_get_analytics_summary_empty(self, db):
        """get_analytics_summary returns zeros when no data."""
        summary = db.get_analytics_summary()
        assert summary["totals"]["views"] == 0
        assert summary["platforms"] == []

    def test_get_top_content(self, db):
        """get_top_content returns items sorted by views desc."""
        _seed_task(db, "task-1", "youtube")
        _seed_task(db, "task-2", "bilibili")
        db.upsert_content_analytics(
            publish_task_id="task-1", platform="youtube",
            post_id="p1", views=500,
        )
        db.upsert_content_analytics(
            publish_task_id="task-2", platform="bilibili",
            post_id="p2", views=1000,
        )
        top = db.get_top_content(limit=10)
        assert len(top) == 2
        assert top[0]["views"] == 1000
        assert top[1]["views"] == 500

    def test_get_top_content_with_limit(self, db):
        """get_top_content respects the limit parameter."""
        for i in range(5):
            _seed_task(db, f"task-{i}", "youtube", f"acc-{i}")
            db.upsert_content_analytics(
                publish_task_id=f"task-{i}", platform="youtube",
                post_id=f"p-{i}", views=i * 100,
            )
        top = db.get_top_content(limit=2)
        assert len(top) == 2

    def test_get_all_oauth_credentials(self, db):
        """get_all_oauth_credentials returns all stored credentials."""
        _insert_platform_account(db, "acc-1", "youtube")
        db.upsert_oauth_credential(
            account_id="acc-1",
            platform="youtube",
            access_token="tok1",
            refresh_token="ref1",
            expires_at=int(time.time()) + 3600,
        )
        creds = db.get_all_oauth_credentials()
        assert len(creds) == 1
        assert creds[0]["platform"] == "youtube"

    def test_count_publish_tasks_v2_by_status(self, db):
        """count_publish_tasks_v2_by_status groups correctly."""
        _insert_platform_account(db, "acc-1", "youtube")
        for i, status in enumerate(["pending", "pending", "published", "failed"]):
            db.insert_publish_task_v2({
                "id": f"task-{i}",
                "account_id": "acc-1",
                "platform": "youtube",
                "title": f"Test {i}",
                "status": status,
            })
        counts = db.count_publish_tasks_v2_by_status()
        assert counts.get("pending") == 2
        assert counts.get("published") == 1
        assert counts.get("failed") == 1


# ---------------------------------------------------------------------------
# AnalyticsService Tests
# ---------------------------------------------------------------------------

class TestAnalyticsServiceFetchStats:
    @pytest.mark.asyncio
    async def test_fetch_video_stats_success(self, analytics_service, mock_registry, mock_token_manager):
        """fetch_video_stats returns stats from platform service."""
        mock_service = AsyncMock()
        mock_service.get_video_stats.return_value = {"views": 100, "likes": 10}
        mock_registry.get.return_value = mock_service
        mock_token_manager.get_valid_token.return_value = MagicMock()

        result = await analytics_service.fetch_video_stats("acc-1", "youtube", "post-1")
        assert result == {"views": 100, "likes": 10}
        mock_service.get_video_stats.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_video_stats_platform_not_registered(self, analytics_service, mock_registry):
        """fetch_video_stats returns {} when platform not in registry."""
        mock_registry.get.return_value = None
        result = await analytics_service.fetch_video_stats("acc-1", "unknown", "post-1")
        assert result == {}

    @pytest.mark.asyncio
    async def test_fetch_video_stats_not_implemented(self, analytics_service, mock_registry, mock_token_manager):
        """fetch_video_stats returns {} when service raises NotImplementedError."""
        mock_service = AsyncMock()
        mock_service.get_video_stats.side_effect = NotImplementedError
        mock_registry.get.return_value = mock_service
        mock_token_manager.get_valid_token.return_value = MagicMock()

        result = await analytics_service.fetch_video_stats("acc-1", "youtube", "post-1")
        assert result == {}


class TestAnalyticsServiceSync:
    @pytest.mark.asyncio
    async def test_sync_all_stats_mixed(self, db, mock_token_manager, mock_registry):
        """sync_all_stats handles mixed success/skip/fail."""
        _insert_platform_account(db, "acc-1", "youtube")
        # Task with post_id
        db.insert_publish_task_v2({
            "id": "task-1", "account_id": "acc-1", "platform": "youtube",
            "title": "Test 1", "status": "published", "post_id": "post-1",
        })
        # Task without post_id (should be skipped)
        db.insert_publish_task_v2({
            "id": "task-2", "account_id": "acc-1", "platform": "youtube",
            "title": "Test 2", "status": "published", "post_id": "",
        })

        mock_service = AsyncMock()
        mock_service.get_video_stats.return_value = {"views": 100}
        mock_registry.get.return_value = mock_service
        mock_token_manager.get_valid_token.return_value = MagicMock()

        svc = AnalyticsService(db=db, token_manager=mock_token_manager, registry=mock_registry)
        results = await svc.sync_all_stats()
        assert results["synced"] == 1
        assert results["skipped"] == 1
        assert results["failed"] == 0

    @pytest.mark.asyncio
    async def test_sync_all_stats_failure(self, db, mock_token_manager, mock_registry):
        """sync_all_stats increments failed count on exception."""
        _insert_platform_account(db, "acc-1", "youtube")
        db.insert_publish_task_v2({
            "id": "task-1", "account_id": "acc-1", "platform": "youtube",
            "title": "Test 1", "status": "published", "post_id": "post-1",
        })

        mock_service = AsyncMock()
        mock_service.get_video_stats.side_effect = Exception("API error")
        mock_registry.get.return_value = mock_service
        mock_token_manager.get_valid_token.return_value = MagicMock()

        svc = AnalyticsService(db=db, token_manager=mock_token_manager, registry=mock_registry)
        results = await svc.sync_all_stats()
        assert results["failed"] == 1

    @pytest.mark.asyncio
    async def test_sync_all_stats_empty_stats(self, db, mock_token_manager, mock_registry):
        """sync_all_stats skips when fetch returns empty dict."""
        _insert_platform_account(db, "acc-1", "youtube")
        db.insert_publish_task_v2({
            "id": "task-1", "account_id": "acc-1", "platform": "youtube",
            "title": "Test 1", "status": "published", "post_id": "post-1",
        })

        mock_service = AsyncMock()
        mock_service.get_video_stats.return_value = {}
        mock_registry.get.return_value = mock_service
        mock_token_manager.get_valid_token.return_value = MagicMock()

        svc = AnalyticsService(db=db, token_manager=mock_token_manager, registry=mock_registry)
        results = await svc.sync_all_stats()
        assert results["skipped"] == 1


class TestAnalyticsServiceSummary:
    def test_get_analytics_summary(self, analytics_service, db):
        """get_analytics_summary delegates to db."""
        _seed_task(db, "task-1", "youtube")
        db.upsert_content_analytics(
            publish_task_id="task-1", platform="youtube",
            post_id="p1", views=100,
        )
        summary = analytics_service.get_analytics_summary()
        assert summary["totals"]["views"] == 100

    def test_get_task_analytics(self, analytics_service, db):
        """get_task_analytics returns records for specific task."""
        _seed_task(db, "task-1", "youtube")
        db.upsert_content_analytics(
            publish_task_id="task-1", platform="youtube",
            post_id="p1", views=100,
        )
        records = analytics_service.get_task_analytics("task-1")
        assert len(records) == 1

    def test_get_top_content(self, analytics_service, db):
        """get_top_content delegates to db."""
        _seed_task(db, "task-1", "youtube")
        db.upsert_content_analytics(
            publish_task_id="task-1", platform="youtube",
            post_id="p1", views=100,
        )
        top = analytics_service.get_top_content(limit=5)
        assert len(top) == 1


# ---------------------------------------------------------------------------
# Token Health Tests
# ---------------------------------------------------------------------------

class TestTokenHealth:
    @pytest.mark.asyncio
    async def test_expired_token(self, db):
        """check_all_token_health detects expired tokens."""
        _insert_platform_account(db, "acc-1", "youtube")
        db.upsert_oauth_credential(
            account_id="acc-1",
            platform="youtube",
            access_token="tok",
            refresh_token="ref",
            expires_at=int(time.time()) - 3600,  # expired 1h ago
        )
        tm = TokenManager(db)
        alerts = await tm.check_all_token_health()
        assert len(alerts) == 1
        assert alerts[0]["status"] == "expired"

    @pytest.mark.asyncio
    async def test_expiring_soon_token(self, db):
        """check_all_token_health detects tokens expiring within 7 days."""
        _insert_platform_account(db, "acc-1", "youtube")
        db.upsert_oauth_credential(
            account_id="acc-1",
            platform="youtube",
            access_token="tok",
            refresh_token="ref",
            expires_at=int(time.time()) + 3 * 86400,  # 3 days
        )
        tm = TokenManager(db)
        alerts = await tm.check_all_token_health()
        assert len(alerts) == 1
        assert alerts[0]["status"] == "expiring_soon"
        assert "expires_in_hours" in alerts[0]

    @pytest.mark.asyncio
    async def test_healthy_token(self, db):
        """check_all_token_health returns empty for healthy tokens."""
        _insert_platform_account(db, "acc-1", "youtube")
        db.upsert_oauth_credential(
            account_id="acc-1",
            platform="youtube",
            access_token="tok",
            refresh_token="ref",
            expires_at=int(time.time()) + 30 * 86400,  # 30 days
        )
        tm = TokenManager(db)
        alerts = await tm.check_all_token_health()
        assert len(alerts) == 0

    @pytest.mark.asyncio
    async def test_multiple_tokens_mixed(self, db):
        """check_all_token_health handles multiple tokens with mixed statuses."""
        _insert_platform_account(db, "acc-1", "youtube")
        _insert_platform_account(db, "acc-2", "bilibili")
        db.upsert_oauth_credential(
            account_id="acc-1", platform="youtube",
            access_token="tok1", refresh_token="ref1",
            expires_at=int(time.time()) - 100,  # expired
        )
        db.upsert_oauth_credential(
            account_id="acc-2", platform="bilibili",
            access_token="tok2", refresh_token="ref2",
            expires_at=int(time.time()) + 90 * 86400,  # healthy
        )
        tm = TokenManager(db)
        alerts = await tm.check_all_token_health()
        assert len(alerts) == 1
        assert alerts[0]["status"] == "expired"
