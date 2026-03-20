"""
AsyncDatabase 单元测试
使用 SQLite async 后端 (aiosqlite)，无需真实 PostgreSQL。
"""
import os
import pytest
import pytest_asyncio
from datetime import datetime

# 强制使用内存 SQLite，避免文件冲突
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"

from core.db_engine import (  # noqa: E402
    get_database_url,
    get_engine,
    get_session_factory,
    init_db,
    close_db,
    reset_engine,
    Base,
)
from core.models import (  # noqa: E402
    AccountModel,
    PublishTaskModel,
    PublishJobModel,
    PublishJobEventModel,
)
from core.database_async import AsyncDatabase  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """每个测试前重建数据库"""
    reset_engine()
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"
    await init_db()
    yield
    await close_db()
    reset_engine()


@pytest.fixture
def db():
    return AsyncDatabase()


# ---------------------------------------------------------------------------
# get_database_url 测试
# ---------------------------------------------------------------------------


class TestGetDatabaseUrl:
    def test_env_var(self):
        os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"
        url = get_database_url()
        assert url == "sqlite+aiosqlite://"

    def test_default_fallback(self):
        old = os.environ.pop("DATABASE_URL", None)
        try:
            url = get_database_url()
            assert "sqlite" in url
            assert "video_factory" in url
        finally:
            if old is not None:
                os.environ["DATABASE_URL"] = old


# ---------------------------------------------------------------------------
# ORM 模型表结构测试
# ---------------------------------------------------------------------------


class TestOrmModels:
    def test_accounts_table_columns(self):
        cols = {c.name for c in AccountModel.__table__.columns}
        expected = {
            "id", "platform", "name", "cookie_path", "status",
            "last_test", "created_at", "is_default", "capabilities_json", "last_error",
        }
        assert expected == cols

    def test_publish_tasks_table_columns(self):
        cols = {c.name for c in PublishTaskModel.__table__.columns}
        expected = {
            "id", "task_id", "video_path", "platform", "account_id",
            "title", "description", "tags", "cover_path", "publish_time",
            "status", "publish_url", "error", "created_at", "updated_at",
        }
        assert expected == cols

    def test_publish_jobs_table_columns(self):
        cols = {c.name for c in PublishJobModel.__table__.columns}
        expected = {
            "job_id", "task_id", "platform", "scheduled_time",
            "product_json", "metadata_json", "product_type", "product_identity",
            "idempotency_key", "status", "result_json",
            "retry_count", "max_retries", "created_at", "updated_at",
        }
        assert expected == cols

    def test_publish_job_events_table_columns(self):
        cols = {c.name for c in PublishJobEventModel.__table__.columns}
        expected = {
            "id", "job_id", "task_id", "platform", "event_type",
            "from_status", "to_status", "message", "payload_json", "created_at",
        }
        assert expected == cols

    def test_all_tables_in_metadata(self):
        tables = set(Base.metadata.tables.keys())
        assert {"accounts", "publish_tasks", "publish_jobs", "publish_job_events"} == tables


# ---------------------------------------------------------------------------
# AsyncDatabase CRUD 测试 — accounts
# ---------------------------------------------------------------------------


class TestAccountCrud:
    @pytest.mark.asyncio
    async def test_insert_and_get(self, db):
        await db.insert_account({
            "id": "acc-1",
            "platform": "youtube",
            "name": "Test Account",
            "cookie_path": "/tmp/cookie",
            "status": "active",
            "created_at": "2024-01-01T00:00:00",
        })
        acc = await db.get_account("acc-1")
        assert acc is not None
        assert acc["id"] == "acc-1"
        assert acc["platform"] == "youtube"
        assert acc["name"] == "Test Account"
        assert acc["is_default"] is False

    @pytest.mark.asyncio
    async def test_get_accounts_filter_platform(self, db):
        await db.insert_account({
            "id": "acc-yt", "platform": "youtube", "name": "YT",
            "cookie_path": "", "status": "active", "created_at": "2024-01-01",
        })
        await db.insert_account({
            "id": "acc-bl", "platform": "bilibili", "name": "BL",
            "cookie_path": "", "status": "active", "created_at": "2024-01-01",
        })
        yt = await db.get_accounts(platform="youtube")
        assert len(yt) == 1
        assert yt[0]["platform"] == "youtube"

        all_accounts = await db.get_accounts()
        assert len(all_accounts) == 2

    @pytest.mark.asyncio
    async def test_delete_account(self, db):
        await db.insert_account({
            "id": "acc-del", "platform": "youtube", "name": "Del",
            "cookie_path": "", "status": "active", "created_at": "2024-01-01",
        })
        await db.delete_account("acc-del")
        assert await db.get_account("acc-del") is None

    @pytest.mark.asyncio
    async def test_set_default_account(self, db):
        await db.insert_account({
            "id": "acc-a", "platform": "youtube", "name": "A",
            "cookie_path": "", "status": "active", "created_at": "2024-01-01",
        })
        await db.insert_account({
            "id": "acc-b", "platform": "youtube", "name": "B",
            "cookie_path": "", "status": "active", "created_at": "2024-01-02",
        })
        result = await db.set_default_account("acc-b")
        assert result is True
        b = await db.get_account("acc-b")
        assert b["is_default"] is True
        a = await db.get_account("acc-a")
        assert a["is_default"] is False

    @pytest.mark.asyncio
    async def test_set_default_nonexistent(self, db):
        result = await db.set_default_account("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_update_account_validation(self, db):
        await db.insert_account({
            "id": "acc-v", "platform": "youtube", "name": "V",
            "cookie_path": "", "status": "active", "created_at": "2024-01-01",
        })
        tested = datetime(2024, 6, 15, 12, 0, 0)
        await db.update_account_validation(
            "acc-v",
            status="validated",
            capabilities={"upload": True},
            last_error="",
            tested_at=tested,
        )
        acc = await db.get_account("acc-v")
        assert acc["status"] == "validated"
        assert acc["capabilities"] == {"upload": True}
        assert acc["last_test"] == tested.isoformat()


# ---------------------------------------------------------------------------
# AsyncDatabase CRUD 测试 — publish_tasks
# ---------------------------------------------------------------------------


class TestPublishTaskCrud:
    @pytest.mark.asyncio
    async def test_insert_and_get(self, db):
        now = datetime.now().isoformat()
        await db.insert_publish_task({
            "id": "pt-1", "task_id": "t-1", "video_path": "/video.mp4",
            "platform": "youtube", "account_id": "acc-1", "title": "Test",
            "status": "pending", "created_at": now, "updated_at": now,
        })
        task = await db.get_publish_task("pt-1")
        assert task is not None
        assert task["title"] == "Test"

    @pytest.mark.asyncio
    async def test_upsert_publish_task(self, db):
        now = datetime.now().isoformat()
        data = {
            "id": "pt-u", "task_id": "t-u", "video_path": "/v.mp4",
            "platform": "youtube", "account_id": "acc-1", "title": "Original",
            "status": "pending", "created_at": now, "updated_at": now,
        }
        await db.upsert_publish_task(data)
        data["title"] = "Updated"
        data["status"] = "published"
        await db.upsert_publish_task(data)
        task = await db.get_publish_task("pt-u")
        assert task["title"] == "Updated"
        assert task["status"] == "published"

    @pytest.mark.asyncio
    async def test_update_task_status(self, db):
        now = datetime.now().isoformat()
        await db.insert_publish_task({
            "id": "pt-s", "task_id": "t-s", "video_path": "/v.mp4",
            "platform": "youtube", "account_id": "acc-1", "title": "T",
            "status": "pending", "created_at": now, "updated_at": now,
        })
        await db.update_task_status("pt-s", "published")
        task = await db.get_publish_task("pt-s")
        assert task["status"] == "published"

    @pytest.mark.asyncio
    async def test_delete_publish_task(self, db):
        now = datetime.now().isoformat()
        await db.insert_publish_task({
            "id": "pt-d", "task_id": "t-d", "video_path": "/v.mp4",
            "platform": "youtube", "account_id": "acc-1", "title": "Del",
            "status": "pending", "created_at": now, "updated_at": now,
        })
        await db.delete_publish_task("pt-d")
        assert await db.get_publish_task("pt-d") is None


# ---------------------------------------------------------------------------
# AsyncDatabase CRUD 测试 — publish_jobs
# ---------------------------------------------------------------------------


class TestPublishJobCrud:
    @pytest.mark.asyncio
    async def test_upsert_and_get(self, db):
        job = {
            "job_id": "j-1", "task_id": "t-1", "platform": "youtube",
            "scheduled_time": 1700000000.0,
            "product": {"url": "http://example.com"},
            "metadata": {"key": "val"},
            "product_type": "video", "product_identity": "vid-1",
            "idempotency_key": "idem-1", "status": "pending",
            "result": {}, "retry_count": 0, "max_retries": 3,
        }
        await db.upsert_publish_job(job)
        jobs = await db.get_publish_jobs()
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == "j-1"
        assert jobs[0]["product"] == {"url": "http://example.com"}

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, db):
        job = {
            "job_id": "j-u", "task_id": "t-u", "platform": "youtube",
            "scheduled_time": 1700000000.0,
            "product": {}, "product_type": "video",
            "product_identity": "vid-u", "idempotency_key": "idem-u",
            "status": "pending", "result": {}, "retry_count": 0, "max_retries": 3,
        }
        await db.upsert_publish_job(job)
        job["status"] = "completed"
        job["retry_count"] = 1
        await db.upsert_publish_job(job)
        jobs = await db.get_publish_jobs()
        assert len(jobs) == 1
        assert jobs[0]["status"] == "completed"
        assert jobs[0]["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_delete_publish_job(self, db):
        job = {
            "job_id": "j-d", "task_id": "t-d", "platform": "youtube",
            "scheduled_time": 1700000000.0,
            "product": {}, "product_type": "video",
            "product_identity": "vid-d", "idempotency_key": "idem-d",
            "status": "pending", "result": {}, "retry_count": 0, "max_retries": 3,
        }
        await db.upsert_publish_job(job)
        await db.delete_publish_job("j-d")
        jobs = await db.get_publish_jobs()
        assert len(jobs) == 0

    @pytest.mark.asyncio
    async def test_get_publish_jobs_filter(self, db):
        for i in range(3):
            await db.upsert_publish_job({
                "job_id": f"j-f{i}", "task_id": "t-filter",
                "platform": "youtube", "scheduled_time": 1700000000.0 + i,
                "product": {}, "product_type": "video",
                "product_identity": f"vid-f{i}", "idempotency_key": f"idem-f{i}",
                "status": "pending" if i < 2 else "completed",
                "result": {}, "retry_count": 0, "max_retries": 3,
            })
        pending = await db.get_publish_jobs(status="pending")
        assert len(pending) == 2
        by_task = await db.get_publish_jobs(task_id="t-filter")
        assert len(by_task) == 3


# ---------------------------------------------------------------------------
# AsyncDatabase CRUD 测试 — publish_job_events
# ---------------------------------------------------------------------------


class TestPublishJobEventCrud:
    @pytest.mark.asyncio
    async def test_record_and_get(self, db):
        await db.record_publish_job_event({
            "job_id": "j-ev", "task_id": "t-ev", "platform": "youtube",
            "event_type": "status_change", "from_status": "pending",
            "to_status": "running", "message": "Started",
            "payload": {"detail": "ok"},
        })
        events = await db.get_publish_job_events(job_id="j-ev")
        assert len(events) == 1
        assert events[0]["event_type"] == "status_change"
        assert events[0]["payload"] == {"detail": "ok"}

    @pytest.mark.asyncio
    async def test_insert_publish_job_event_kwargs(self, db):
        """测试关键字参数版本（与 Database 接口一致）"""
        await db.insert_publish_job_event(
            job_id="j-kw", task_id="t-kw", platform="bilibili",
            event_type="error", from_status="running", to_status="failed",
            message="Upload failed",
        )
        events = await db.get_publish_job_events(task_id="t-kw")
        assert len(events) == 1
        assert events[0]["platform"] == "bilibili"

    @pytest.mark.asyncio
    async def test_events_limit(self, db):
        for i in range(5):
            await db.record_publish_job_event({
                "job_id": "j-lim", "task_id": "t-lim", "platform": "youtube",
                "event_type": "log", "from_status": "", "to_status": "",
                "message": f"msg-{i}",
            })
        events = await db.get_publish_job_events(job_id="j-lim", limit=3)
        assert len(events) == 3
