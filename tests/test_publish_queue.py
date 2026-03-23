"""
Sprint 3: PublishQueue 单元测试。

测试发布队列的入队、处理、重试、取消和定时检查功能。
"""

import sys
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from platform_services.base import (  # noqa: E402
    AuthMethod, OAuthCredential, PlatformAccount,
    PlatformService, PlatformType, PublishResult,
)
from platform_services.publish_queue import (  # noqa: E402
    PublishQueue, PublishStatus, BASE_RETRY_DELAY, MAX_ATTEMPTS,
)
from platform_services.registry import PlatformRegistry  # noqa: E402
from platform_services.exceptions import PlatformError  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockPlatformService(PlatformService):
    """Mock 平台服务。"""
    platform = PlatformType.YOUTUBE
    auth_method = AuthMethod.OAUTH2

    async def get_auth_url(self, state, **kw):
        return "url"

    async def handle_callback(self, code, state):
        return (
            PlatformAccount(platform=self.platform, platform_uid="u", username="u", nickname="U"),
            OAuthCredential(access_token="t", refresh_token="r", expires_at=0),
        )

    async def refresh_token(self, cred):
        return cred

    async def check_token_status(self, cred):
        return True

    async def publish_video(self, credential, video_path, title, description="", tags=None, cover_path="", **kw):
        return PublishResult(success=True, post_id="post_123", permalink="https://example.com/post_123")


class FailingPlatformService(MockPlatformService):
    """发布时总是失败的 mock 平台服务。"""

    async def publish_video(self, credential, video_path, title, description="", tags=None, cover_path="", **kw):
        raise PlatformError("模拟发布失败")


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


def _make_token_manager_mock():
    """创建 mock token manager。"""
    tm = MagicMock()
    tm.get_valid_token = AsyncMock(return_value=OAuthCredential(
        access_token="valid_token",
        refresh_token="rt",
        expires_at=9999999999,
    ))
    return tm


def _make_registry_mock(service=None):
    """创建 mock 注册表。"""
    registry = MagicMock()
    registry.get.return_value = service
    return registry


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPublishQueueEnqueue:
    @pytest.mark.asyncio
    async def test_enqueue_creates_task_in_db(self):
        """enqueue 应该在数据库中创建任务。"""
        db = _make_db_mock()
        tm = _make_token_manager_mock()
        registry = _make_registry_mock()
        queue = PublishQueue(db, tm, registry)

        task_data = {
            "account_id": "acc_1",
            "platform": "youtube",
            "title": "Test Video",
            "description": "desc",
            "tags": ["tag1"],
            "video_path": "/tmp/video.mp4",
        }
        task_id = await queue.enqueue(task_data)

        assert task_id is not None
        stored = db.get_publish_task_v2(task_id)
        assert stored is not None
        assert stored["title"] == "Test Video"
        assert stored["status"] == PublishStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_enqueue_with_future_scheduled_at_sets_scheduled(self):
        """enqueue 带未来时间的 scheduled_at 应标记为 scheduled。"""
        db = _make_db_mock()
        tm = _make_token_manager_mock()
        registry = _make_registry_mock()
        queue = PublishQueue(db, tm, registry)

        future = (datetime.now() + timedelta(hours=1)).isoformat()
        task_data = {
            "account_id": "acc_1",
            "platform": "youtube",
            "title": "Scheduled Video",
            "scheduled_at": future,
        }
        task_id = await queue.enqueue(task_data)

        stored = db.get_publish_task_v2(task_id)
        assert stored["status"] == PublishStatus.SCHEDULED.value

    @pytest.mark.asyncio
    async def test_enqueue_with_past_scheduled_at_sets_pending(self):
        """enqueue 带过去时间的 scheduled_at 应标记为 pending。"""
        db = _make_db_mock()
        tm = _make_token_manager_mock()
        registry = _make_registry_mock()
        queue = PublishQueue(db, tm, registry)

        past = (datetime.now() - timedelta(hours=1)).isoformat()
        task_data = {
            "account_id": "acc_1",
            "platform": "youtube",
            "title": "Past Video",
            "scheduled_at": past,
        }
        task_id = await queue.enqueue(task_data)

        stored = db.get_publish_task_v2(task_id)
        assert stored["status"] == PublishStatus.PENDING.value


class TestPublishQueueProcess:
    @pytest.mark.asyncio
    async def test_process_task_success(self):
        """_process_task 成功时应标记为 published。"""
        db = _make_db_mock()
        tm = _make_token_manager_mock()
        service = MockPlatformService()
        registry = _make_registry_mock(service)
        queue = PublishQueue(db, tm, registry)

        task_data = {
            "id": "task_success",
            "account_id": "acc_1",
            "platform": "youtube",
            "title": "Success Video",
            "video_path": "/tmp/video.mp4",
            "status": "pending",
            "attempts": 0,
            "max_attempts": 3,
            "tags": [],
            "platform_options": {},
        }
        db.insert_publish_task_v2(task_data)

        await queue._process_task("task_success")

        stored = db.get_publish_task_v2("task_success")
        assert stored["status"] == PublishStatus.PUBLISHED.value
        assert stored["post_id"] == "post_123"
        assert stored["permalink"] == "https://example.com/post_123"

    @pytest.mark.asyncio
    async def test_process_task_failure_with_retry(self):
        """_process_task 失败时应重试（指数退避）。"""
        db = _make_db_mock()
        tm = _make_token_manager_mock()
        service = FailingPlatformService()
        registry = _make_registry_mock(service)
        queue = PublishQueue(db, tm, registry)

        task_data = {
            "id": "task_fail_retry",
            "account_id": "acc_1",
            "platform": "youtube",
            "title": "Fail Video",
            "video_path": "/tmp/video.mp4",
            "status": "pending",
            "attempts": 0,
            "max_attempts": 3,
            "tags": [],
            "platform_options": {},
        }
        db.insert_publish_task_v2(task_data)

        with patch("platform_services.publish_queue.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await queue._process_task("task_fail_retry")

        stored = db.get_publish_task_v2("task_fail_retry")
        # After first failure: attempts=1, status should be pending (retry)
        assert stored["attempts"] == 1
        assert stored["status"] == PublishStatus.PENDING.value
        # Verify exponential backoff: delay = 5 * 2^(1-1) = 5 seconds
        mock_sleep.assert_called_once_with(BASE_RETRY_DELAY * (2 ** 0))

    @pytest.mark.asyncio
    async def test_process_task_failure_after_max_attempts(self):
        """达到最大重试次数后应标记为 failed。"""
        db = _make_db_mock()
        tm = _make_token_manager_mock()
        service = FailingPlatformService()
        registry = _make_registry_mock(service)
        queue = PublishQueue(db, tm, registry)

        task_data = {
            "id": "task_max_fail",
            "account_id": "acc_1",
            "platform": "youtube",
            "title": "Max Fail Video",
            "video_path": "/tmp/video.mp4",
            "status": "pending",
            "attempts": 2,  # Already failed twice
            "max_attempts": 3,
            "tags": [],
            "platform_options": {},
        }
        db.insert_publish_task_v2(task_data)

        await queue._process_task("task_max_fail")

        stored = db.get_publish_task_v2("task_max_fail")
        assert stored["status"] == PublishStatus.FAILED.value
        assert stored["attempts"] == 3
        assert "模拟发布失败" in stored["error_message"]

    @pytest.mark.asyncio
    async def test_exponential_backoff_delay_calculation(self):
        """验证指数退避延迟计算：delay = 5 * 2^attempt。"""
        db = _make_db_mock()
        tm = _make_token_manager_mock()
        service = FailingPlatformService()
        registry = _make_registry_mock(service)
        queue = PublishQueue(db, tm, registry)

        # Test with attempts=1 (second failure)
        task_data = {
            "id": "task_backoff",
            "account_id": "acc_1",
            "platform": "youtube",
            "title": "Backoff Video",
            "video_path": "/tmp/video.mp4",
            "status": "pending",
            "attempts": 1,
            "max_attempts": 5,
            "tags": [],
            "platform_options": {},
        }
        db.insert_publish_task_v2(task_data)

        with patch("platform_services.publish_queue.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await queue._process_task("task_backoff")

        # delay = 5 * 2^(2-1) = 10 seconds
        mock_sleep.assert_called_once_with(BASE_RETRY_DELAY * (2 ** 1))

    @pytest.mark.asyncio
    async def test_process_task_no_platform_registered(self):
        """平台未注册应标记为 failed。"""
        db = _make_db_mock()
        tm = _make_token_manager_mock()
        registry = _make_registry_mock(None)  # No service registered
        queue = PublishQueue(db, tm, registry)

        task_data = {
            "id": "task_no_platform",
            "account_id": "acc_1",
            "platform": "unknown_platform",
            "title": "No Platform",
            "video_path": "/tmp/video.mp4",
            "status": "pending",
            "attempts": 0,
            "max_attempts": 3,
            "tags": [],
            "platform_options": {},
        }
        db.insert_publish_task_v2(task_data)

        await queue._process_task("task_no_platform")

        stored = db.get_publish_task_v2("task_no_platform")
        assert stored["status"] == PublishStatus.FAILED.value
        assert "未注册" in stored["error_message"]


class TestPublishQueueRetryCancel:
    @pytest.mark.asyncio
    async def test_retry_task_resets_and_requeues(self):
        """retry_task 应重置 failed 任务并重新入队。"""
        db = _make_db_mock()
        tm = _make_token_manager_mock()
        registry = _make_registry_mock()
        queue = PublishQueue(db, tm, registry)

        task_data = {
            "id": "task_retry",
            "account_id": "acc_1",
            "platform": "youtube",
            "title": "Retry Video",
            "status": "failed",
            "attempts": 3,
            "error_message": "previous error",
        }
        db.insert_publish_task_v2(task_data)

        ok = await queue.retry_task("task_retry")
        assert ok is True

        stored = db.get_publish_task_v2("task_retry")
        assert stored["status"] == PublishStatus.PENDING.value
        assert stored["attempts"] == 0
        assert stored["error_message"] == ""
        # Task should be in the queue
        assert not queue._queue.empty()

    @pytest.mark.asyncio
    async def test_retry_task_non_failed_returns_false(self):
        """retry_task 非 failed 状态应返回 False。"""
        db = _make_db_mock()
        tm = _make_token_manager_mock()
        registry = _make_registry_mock()
        queue = PublishQueue(db, tm, registry)

        task_data = {
            "id": "task_not_failed",
            "account_id": "acc_1",
            "platform": "youtube",
            "title": "Not Failed",
            "status": "pending",
        }
        db.insert_publish_task_v2(task_data)

        ok = await queue.retry_task("task_not_failed")
        assert ok is False

    @pytest.mark.asyncio
    async def test_cancel_task_sets_cancelled(self):
        """cancel_task 应标记为 cancelled。"""
        db = _make_db_mock()
        tm = _make_token_manager_mock()
        registry = _make_registry_mock()
        queue = PublishQueue(db, tm, registry)

        task_data = {
            "id": "task_cancel",
            "account_id": "acc_1",
            "platform": "youtube",
            "title": "Cancel Video",
            "status": "pending",
        }
        db.insert_publish_task_v2(task_data)

        ok = await queue.cancel_task("task_cancel")
        assert ok is True

        stored = db.get_publish_task_v2("task_cancel")
        assert stored["status"] == PublishStatus.CANCELLED.value

    @pytest.mark.asyncio
    async def test_cancel_published_task_returns_false(self):
        """cancel_task 对已发布的任务应返回 False。"""
        db = _make_db_mock()
        tm = _make_token_manager_mock()
        registry = _make_registry_mock()
        queue = PublishQueue(db, tm, registry)

        task_data = {
            "id": "task_published",
            "account_id": "acc_1",
            "platform": "youtube",
            "title": "Published",
            "status": "published",
        }
        db.insert_publish_task_v2(task_data)

        ok = await queue.cancel_task("task_published")
        assert ok is False

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task_returns_false(self):
        """cancel_task 不存在的任务应返回 False。"""
        db = _make_db_mock()
        tm = _make_token_manager_mock()
        registry = _make_registry_mock()
        queue = PublishQueue(db, tm, registry)

        ok = await queue.cancel_task("nonexistent")
        assert ok is False


class TestPublishQueueScheduleChecker:
    @pytest.mark.asyncio
    async def test_schedule_checker_moves_due_tasks(self):
        """_schedule_checker 应将到期任务入队。"""
        db = _make_db_mock()
        tm = _make_token_manager_mock()
        registry = _make_registry_mock()
        queue = PublishQueue(db, tm, registry)
        queue._running = True

        # Insert a scheduled task with a past scheduled_at
        past = (datetime.now() - timedelta(minutes=5)).isoformat()
        task_data = {
            "id": "task_scheduled_due",
            "account_id": "acc_1",
            "platform": "youtube",
            "title": "Due Task",
            "status": "scheduled",
            "scheduled_at": past,
        }
        db.insert_publish_task_v2(task_data)

        # Patch sleep to run once then stop
        call_count = 0

        async def fake_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                queue._running = False

        with patch("platform_services.publish_queue.asyncio.sleep", side_effect=fake_sleep):
            await queue._schedule_checker()

        stored = db.get_publish_task_v2("task_scheduled_due")
        assert stored["status"] == PublishStatus.PENDING.value
        assert not queue._queue.empty()
