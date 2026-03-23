"""
PublishQueue 单元测试。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from platform_services.publish_queue import PublishQueue, PublishStatus
from platform_services.base import OAuthCredential, PublishResult, PlatformType, AuthMethod
from platform_services.exceptions import PlatformError, TokenExpiredError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db():
    """Create a mock Database with v2 task CRUD methods."""
    db = MagicMock()
    db._tasks = {}  # in-memory store for tests

    def insert_task(task):
        db._tasks[task["id"]] = dict(task)

    def get_task(task_id):
        t = db._tasks.get(task_id)
        if t is None:
            return None
        result = dict(t)
        if isinstance(result.get("tags"), str):
            result["tags"] = json.loads(result["tags"])
        if isinstance(result.get("platform_options"), str):
            result["platform_options"] = json.loads(result["platform_options"])
        return result

    def get_tasks(*, platform=None, status=None, account_id=None, limit=100):
        results = []
        for t in db._tasks.values():
            if platform and t.get("platform") != platform:
                continue
            if status and t.get("status") != status:
                continue
            if account_id and t.get("account_id") != account_id:
                continue
            results.append(dict(t))
        return results[:limit]

    def update_task(task_id, **fields):
        if task_id in db._tasks:
            db._tasks[task_id].update(fields)

    def delete_task(task_id):
        db._tasks.pop(task_id, None)

    db.insert_publish_task_v2 = MagicMock(side_effect=insert_task)
    db.get_publish_task_v2 = MagicMock(side_effect=get_task)
    db.get_publish_tasks_v2 = MagicMock(side_effect=get_tasks)
    db.update_publish_task_v2 = MagicMock(side_effect=update_task)
    db.delete_publish_task_v2 = MagicMock(side_effect=delete_task)
    return db


@pytest.fixture
def mock_token_manager():
    """Create a mock TokenManager."""
    tm = MagicMock()
    tm.get_valid_token = AsyncMock(return_value=OAuthCredential(
        access_token="test_token",
        refresh_token="test_refresh",
        expires_at=int(datetime.now().timestamp()) + 3600,
    ))
    return tm


@pytest.fixture
def mock_registry():
    """Create a mock PlatformRegistry."""
    registry = MagicMock()

    service = MagicMock()
    service.platform = PlatformType.YOUTUBE
    service.auth_method = AuthMethod.OAUTH2
    service.publish_video = AsyncMock(return_value=PublishResult(
        success=True,
        post_id="video123",
        permalink="https://youtube.com/watch?v=video123",
    ))

    registry.get = MagicMock(return_value=service)
    return registry


@pytest.fixture
def queue(mock_db, mock_token_manager, mock_registry):
    """Create a PublishQueue with mocked dependencies."""
    return PublishQueue(
        db=mock_db,
        token_manager=mock_token_manager,
        registry=mock_registry,
    )


def _make_task_data(platform="youtube", account_id=None, scheduled_at=None):
    return {
        "id": str(uuid.uuid4()),
        "account_id": account_id or str(uuid.uuid4()),
        "platform": platform,
        "title": "Test Video",
        "description": "Test description",
        "tags": ["tag1", "tag2"],
        "video_path": "/tmp/test.mp4",
        "cover_path": "",
        "platform_options": {},
        "scheduled_at": scheduled_at,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enqueue_creates_task_in_db(queue, mock_db):
    """enqueue should insert a task into the database with status 'pending'."""
    task_data = _make_task_data()
    task_id = await queue.enqueue(task_data)

    assert task_id == task_data["id"]
    mock_db.insert_publish_task_v2.assert_called_once()
    inserted = mock_db.insert_publish_task_v2.call_args[0][0]
    assert inserted["status"] == PublishStatus.PENDING.value


@pytest.mark.asyncio
async def test_enqueue_with_scheduled_at_sets_scheduled_status(queue, mock_db):
    """enqueue with a future scheduled_at should set status to 'scheduled'."""
    future = (datetime.now() + timedelta(hours=1)).isoformat()
    task_data = _make_task_data(scheduled_at=future)
    task_id = await queue.enqueue(task_data)

    assert task_id == task_data["id"]
    inserted = mock_db.insert_publish_task_v2.call_args[0][0]
    assert inserted["status"] == PublishStatus.SCHEDULED.value


@pytest.mark.asyncio
async def test_process_task_success(queue, mock_db, mock_token_manager, mock_registry):
    """_process_task should publish and update status to 'published' on success."""
    task_data = _make_task_data()
    task_data["status"] = "pending"
    task_data["attempts"] = 0
    task_data["max_attempts"] = 3
    mock_db._tasks[task_data["id"]] = task_data

    await queue._process_task(task_data["id"])

    # Verify publish_video was called
    service = mock_registry.get.return_value
    service.publish_video.assert_called_once()

    # Verify final status is published
    task = mock_db._tasks[task_data["id"]]
    assert task["status"] == PublishStatus.PUBLISHED.value
    assert task["post_id"] == "video123"


@pytest.mark.asyncio
async def test_process_task_failure_with_retry(queue, mock_db, mock_token_manager, mock_registry):
    """_process_task failure should re-enqueue with exponential backoff when attempts < max."""
    task_data = _make_task_data()
    task_data["status"] = "pending"
    task_data["attempts"] = 0
    task_data["max_attempts"] = 3
    mock_db._tasks[task_data["id"]] = task_data

    # Make publish_video fail
    service = mock_registry.get.return_value
    service.publish_video = AsyncMock(return_value=PublishResult(
        success=False, error="Upload failed",
    ))

    # Patch asyncio.sleep to avoid actual waiting
    with patch("platform_services.publish_queue.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await queue._process_task(task_data["id"])

        # Should have called sleep with exponential backoff: 5 * 2^1 = 10
        mock_sleep.assert_called_once_with(10)

    # Task should be pending (re-enqueued) with attempts=1
    task = mock_db._tasks[task_data["id"]]
    assert task["status"] == PublishStatus.PENDING.value
    assert task["attempts"] == 1


@pytest.mark.asyncio
async def test_process_task_failure_after_max_attempts(queue, mock_db, mock_token_manager, mock_registry):
    """_process_task should mark as 'failed' when attempts reach max_attempts."""
    task_data = _make_task_data()
    task_data["status"] = "pending"
    task_data["attempts"] = 2  # already at max-1
    task_data["max_attempts"] = 3
    mock_db._tasks[task_data["id"]] = task_data

    service = mock_registry.get.return_value
    service.publish_video = AsyncMock(return_value=PublishResult(
        success=False, error="Upload failed final",
    ))

    await queue._process_task(task_data["id"])

    task = mock_db._tasks[task_data["id"]]
    assert task["status"] == PublishStatus.FAILED.value
    assert task["attempts"] == 3


@pytest.mark.asyncio
async def test_retry_task_resets_and_reenqueues(queue, mock_db):
    """retry_task should reset a failed task to pending and re-enqueue."""
    task_data = _make_task_data()
    task_data["status"] = PublishStatus.FAILED.value
    task_data["attempts"] = 3
    task_data["error_message"] = "some error"
    mock_db._tasks[task_data["id"]] = task_data

    result = await queue.retry_task(task_data["id"])
    assert result is True

    task = mock_db._tasks[task_data["id"]]
    assert task["status"] == PublishStatus.PENDING.value
    assert task["attempts"] == 0


@pytest.mark.asyncio
async def test_retry_task_returns_false_for_non_failed(queue, mock_db):
    """retry_task should return False for non-failed tasks."""
    task_data = _make_task_data()
    task_data["status"] = PublishStatus.PUBLISHED.value
    mock_db._tasks[task_data["id"]] = task_data

    result = await queue.retry_task(task_data["id"])
    assert result is False


@pytest.mark.asyncio
async def test_cancel_task_sets_cancelled(queue, mock_db):
    """cancel_task should set status to 'cancelled' for pending tasks."""
    task_data = _make_task_data()
    task_data["status"] = PublishStatus.PENDING.value
    mock_db._tasks[task_data["id"]] = task_data

    result = await queue.cancel_task(task_data["id"])
    assert result is True

    task = mock_db._tasks[task_data["id"]]
    assert task["status"] == PublishStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_cancel_task_returns_false_for_published(queue, mock_db):
    """cancel_task should return False for already published tasks."""
    task_data = _make_task_data()
    task_data["status"] = PublishStatus.PUBLISHED.value
    mock_db._tasks[task_data["id"]] = task_data

    result = await queue.cancel_task(task_data["id"])
    assert result is False


@pytest.mark.asyncio
async def test_schedule_checker_moves_due_tasks(queue, mock_db):
    """_schedule_checker should enqueue tasks whose scheduled_at has passed."""
    past = (datetime.now() - timedelta(minutes=5)).isoformat()
    task_data = _make_task_data(scheduled_at=past)
    task_data["status"] = PublishStatus.SCHEDULED.value
    mock_db._tasks[task_data["id"]] = task_data

    queue._running = True

    # Run one iteration of schedule checking manually
    now = datetime.now().isoformat()
    tasks = mock_db.get_publish_tasks_v2(status=PublishStatus.SCHEDULED.value)
    for task in tasks:
        scheduled_at = task.get("scheduled_at")
        if scheduled_at and scheduled_at <= now:
            mock_db.update_publish_task_v2(
                task["id"],
                status=PublishStatus.PENDING.value,
            )
            await queue._queue.put(task["id"])

    # Verify task was moved to pending
    updated = mock_db._tasks[task_data["id"]]
    assert updated["status"] == PublishStatus.PENDING.value
    assert not queue._queue.empty()


@pytest.mark.asyncio
async def test_exponential_backoff_delay_calculation(queue, mock_db, mock_registry, mock_token_manager):
    """Verify exponential backoff delay: 5 * 2^attempt."""
    task_data = _make_task_data()
    task_data["status"] = "pending"
    task_data["attempts"] = 1  # second attempt → delay = 5 * 2^2 = 20
    task_data["max_attempts"] = 3
    mock_db._tasks[task_data["id"]] = task_data

    service = mock_registry.get.return_value
    service.publish_video = AsyncMock(return_value=PublishResult(
        success=False, error="fail",
    ))

    with patch("platform_services.publish_queue.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await queue._process_task(task_data["id"])
        # attempts was 1, after +1 = 2: delay = 5 * 2^2 = 20
        mock_sleep.assert_called_once_with(20)
