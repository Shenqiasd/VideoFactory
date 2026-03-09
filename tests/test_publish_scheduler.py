import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.config import Config  # noqa: E402
from core.database import Database  # noqa: E402
from core.notification import NotificationManager  # noqa: E402
from core.task import Task, TaskStore, TaskState  # noqa: E402
from distribute.publisher import PublishManager  # noqa: E402
from distribute.scheduler import PublishScheduler  # noqa: E402


class DummyPublishManager:
    def __init__(self, success: bool):
        self.success = success

    async def publish_to_platform(self, **kwargs):
        if self.success:
            return {"success": True, "url": "https://example.com/video/1", "error": ""}
        return {"success": False, "url": "", "error": "mock failure"}


class PlatformAwarePublishManager:
    def __init__(self, responses):
        self.responses = responses

    async def publish_to_platform(self, **kwargs):
        platform = kwargs["platform"]
        return self.responses[platform]


def _build_task() -> Task:
    task = Task(task_id="vf_test_case", source_url="https://example.com", source_title="demo")
    task.state = TaskState.READY_TO_PUBLISH.value
    task.products = [
        {
            "type": "long_video",
            "platform": "all",
            "local_path": "/tmp/video.mp4",
            "title": "title",
            "description": "desc",
            "tags": ["a"],
        }
    ]
    return task


def test_schedule_immediate_idempotent():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = TaskStore(store_path=str(Path(tmpdir) / "tasks.json"))
        scheduler = PublishScheduler(
            task_store=store,
            publish_manager=DummyPublishManager(success=True),
            notifier=NotificationManager(),
            queue_file=str(Path(tmpdir) / "publish_queue.json"),
            db_path=str(Path(tmpdir) / "video_factory.db"),
        )
        task = _build_task()

        first = scheduler.schedule_immediate(task, platforms=["bilibili", "youtube"])
        second = scheduler.schedule_immediate(task, platforms=["bilibili", "youtube"])

        assert first["added"] == 2
        assert first["skipped"] == 0
        assert second["added"] == 0
        assert second["skipped"] == 2


def test_failed_job_can_retry_and_replay():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = TaskStore(store_path=str(Path(tmpdir) / "tasks.json"))
        task = _build_task()
        task.state = TaskState.PUBLISHING.value
        store.update(task)

        scheduler = PublishScheduler(
            task_store=store,
            publish_manager=DummyPublishManager(success=False),
            notifier=NotificationManager(),
            queue_file=str(Path(tmpdir) / "publish_queue.json"),
            db_path=str(Path(tmpdir) / "video_factory.db"),
        )

        scheduler.schedule_immediate(task, platforms=["bilibili"])
        job = scheduler._queue[0]

        asyncio.run(scheduler._execute_job(job))
        assert job.status == "pending"
        assert job.retry_count == 1

        job.status = "failed"
        job.retry_count = job.max_retries
        replayed = scheduler.replay_failed(task.task_id, platform="bilibili")
        assert replayed == 1
        assert job.status == "pending"
        assert job.retry_count == 0


def test_manual_job_requires_confirmation():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = TaskStore(store_path=str(Path(tmpdir) / "tasks.json"))
        task = _build_task()
        task.state = TaskState.PUBLISHING.value
        store.update(task)

        scheduler = PublishScheduler(
            task_store=store,
            publish_manager=PlatformAwarePublishManager(
                {
                    "bilibili": {
                        "success": True,
                        "url": "",
                        "error": "",
                        "manual_checklist": {
                            "platform": "bilibili",
                            "video_path": "/tmp/video.mp4",
                            "title": "title",
                        },
                    }
                }
            ),
            notifier=NotificationManager(),
            queue_file=str(Path(tmpdir) / "publish_queue.json"),
            db_path=str(Path(tmpdir) / "video_factory.db"),
        )

        scheduler.schedule_immediate(task, platforms=["bilibili"])
        job = scheduler._queue[0]
        asyncio.run(scheduler._execute_job(job))

        assert job.status == "manual_pending"
        assert job.result["manual_checklist"]["video_path"] == "/tmp/video.mp4"

        reloaded = PublishScheduler(
            task_store=store,
            publish_manager=DummyPublishManager(success=True),
            notifier=NotificationManager(),
            queue_file=str(Path(tmpdir) / "publish_queue.json"),
            db_path=str(Path(tmpdir) / "video_factory.db"),
        )
        assert reloaded._queue[0].status == "manual_pending"

        asyncio.run(
            reloaded.mark_manual_result(
                task_id=task.task_id,
                job_id=reloaded._queue[0].job_id,
                success=True,
                publish_url="https://example.com/manual/1",
            )
        )

        updated_task = store.get(task.task_id)
        assert updated_task.state == TaskState.COMPLETED.value
        assert reloaded._queue[0].status == "done"
        assert reloaded._queue[0].result["confirmed_manually"] is True


def test_partial_failure_marks_task_failed():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = TaskStore(store_path=str(Path(tmpdir) / "tasks.json"))
        task = _build_task()
        task.state = TaskState.PUBLISHING.value
        store.update(task)

        scheduler = PublishScheduler(
            task_store=store,
            publish_manager=PlatformAwarePublishManager(
                {
                    "bilibili": {"success": True, "url": "https://example.com/b", "error": ""},
                    "youtube": {"success": False, "url": "", "error": "mock failure"},
                }
            ),
            notifier=NotificationManager(),
            queue_file=str(Path(tmpdir) / "publish_queue.json"),
            db_path=str(Path(tmpdir) / "video_factory.db"),
        )

        scheduler.schedule_immediate(task, platforms=["bilibili", "youtube"])
        for job in scheduler._queue:
            if job.platform == "youtube":
                job.max_retries = 0

        for job in scheduler._queue:
            asyncio.run(scheduler._execute_job(job))

        updated_task = store.get(task.task_id)
        assert updated_task.state == TaskState.PARTIAL_SUCCESS.value
        assert "成功 1/2" in updated_task.error_message


def test_cancel_persists_job_status():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = TaskStore(store_path=str(Path(tmpdir) / "tasks.json"))
        task = _build_task()
        task.state = TaskState.PUBLISHING.value
        store.update(task)

        scheduler = PublishScheduler(
            task_store=store,
            publish_manager=DummyPublishManager(success=True),
            notifier=NotificationManager(),
            queue_file=str(Path(tmpdir) / "publish_queue.json"),
            db_path=str(Path(tmpdir) / "video_factory.db"),
        )

        scheduler.schedule_immediate(task, platforms=["bilibili"])
        job = scheduler._queue[0]
        cancelled = scheduler.cancel(task.task_id, job_id=job.job_id)

        assert cancelled == 1

        reloaded = PublishScheduler(
            task_store=store,
            publish_manager=DummyPublishManager(success=True),
            notifier=NotificationManager(),
            queue_file=str(Path(tmpdir) / "publish_queue.json"),
            db_path=str(Path(tmpdir) / "video_factory.db"),
        )
        assert reloaded._queue[0].status == "cancelled"
        assert reloaded._queue[0].result["error"] == "用户取消"


def test_scheduler_migrates_legacy_json_queue_to_sqlite():
    with tempfile.TemporaryDirectory() as tmpdir:
        queue_file = Path(tmpdir) / "publish_queue.json"
        legacy_job = {
            "task_id": "vf_legacy",
            "platform": "bilibili",
            "scheduled_time": 123.0,
            "product": {"type": "long_video", "local_path": "/tmp/video.mp4", "title": "legacy"},
            "metadata": {},
            "product_type": "long_video",
            "product_identity": "/tmp/video.mp4",
            "idempotency_key": "legacy-key",
            "status": "pending",
            "result": {},
            "retry_count": 0,
            "max_retries": 2,
        }
        queue_file.write_text(json.dumps([legacy_job]), encoding="utf-8")

        scheduler = PublishScheduler(
            task_store=TaskStore(store_path=str(Path(tmpdir) / "tasks.json")),
            publish_manager=DummyPublishManager(success=True),
            notifier=NotificationManager(),
            queue_file=str(queue_file),
            db_path=str(Path(tmpdir) / "video_factory.db"),
        )

        assert len(scheduler._queue) == 1
        assert scheduler._queue[0].job_id.startswith("pubjob_")

        reloaded = PublishScheduler(
            task_store=TaskStore(store_path=str(Path(tmpdir) / "tasks.json")),
            publish_manager=DummyPublishManager(success=True),
            notifier=NotificationManager(),
            queue_file=str(queue_file),
            db_path=str(Path(tmpdir) / "video_factory.db"),
        )
        assert len(reloaded._queue) == 1
        assert reloaded._queue[0].job_id == scheduler._queue[0].job_id


def test_publish_manager_requires_valid_default_account(monkeypatch, tmp_path):
    db_path = tmp_path / "video_factory.db"
    cookie_path = tmp_path / "cookie.json"
    cookie_path.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("VF_DB_PATH", str(db_path))
    Config.reset()
    db = Database(db_path=str(db_path))
    db.insert_account(
        {
            "id": "acc_1",
            "platform": "bilibili",
            "name": "B站主号",
            "cookie_path": str(cookie_path),
            "status": "active",
            "last_test": None,
            "created_at": "2026-03-09T14:30:00",
            "is_default": True,
            "capabilities": {
                "platform_supported": True,
                "cookie_required": True,
                "cookie_exists": True,
                "can_auto_publish": True,
                "can_manual_publish": True,
            },
            "last_error": "",
        }
    )

    manager = PublishManager()
    ok = asyncio.run(
        manager.publish_to_platform(
            platform="bilibili",
            video_path="/tmp/video.mp4",
            title="demo",
            task_id="vf_account_bind",
            job_id="pubjob_demo",
        )
    )
    assert ok["success"] is True
    assert ok["manual_checklist"]["account"]["id"] == "acc_1"

    db.update_account_validation(
        "acc_1",
        status="invalid",
        capabilities={
            "platform_supported": True,
            "cookie_required": True,
            "cookie_exists": False,
            "can_auto_publish": False,
            "can_manual_publish": False,
        },
        last_error="Cookie 文件不存在或未配置",
    )
    manager = PublishManager()
    failed = asyncio.run(
        manager.publish_to_platform(
            platform="bilibili",
            video_path="/tmp/video.mp4",
            title="demo",
            task_id="vf_account_bind",
            job_id="pubjob_demo",
        )
    )
    assert failed["success"] is False
    assert "Cookie" in failed["error"]
    monkeypatch.delenv("VF_DB_PATH", raising=False)


def test_publish_manager_prefers_explicit_account_binding(monkeypatch, tmp_path):
    db_path = tmp_path / "video_factory.db"
    cookie_a = tmp_path / "cookie_a.json"
    cookie_b = tmp_path / "cookie_b.json"
    cookie_a.write_text("{}", encoding="utf-8")
    cookie_b.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("VF_DB_PATH", str(db_path))
    Config.reset()
    db = Database(db_path=str(db_path))
    db.insert_account(
        {
            "id": "acc_default",
            "platform": "bilibili",
            "name": "默认账号",
            "cookie_path": str(cookie_a),
            "status": "active",
            "last_test": None,
            "created_at": "2026-03-09T14:30:00",
            "is_default": True,
            "capabilities": {
                "platform_supported": True,
                "cookie_required": True,
                "cookie_exists": True,
                "can_auto_publish": True,
                "can_manual_publish": True,
            },
            "last_error": "",
        }
    )
    db.insert_account(
        {
            "id": "acc_selected",
            "platform": "bilibili",
            "name": "指定账号",
            "cookie_path": str(cookie_b),
            "status": "active",
            "last_test": None,
            "created_at": "2026-03-09T14:31:00",
            "is_default": False,
            "capabilities": {
                "platform_supported": True,
                "cookie_required": True,
                "cookie_exists": True,
                "can_auto_publish": True,
                "can_manual_publish": True,
            },
            "last_error": "",
        }
    )

    manager = PublishManager()
    ok = asyncio.run(
        manager.publish_to_platform(
            platform="bilibili",
            video_path="/tmp/video.mp4",
            title="demo",
            task_id="vf_selected_account",
            job_id="pubjob_selected_account",
            account_id="acc_selected",
        )
    )
    assert ok["success"] is True
    assert ok["manual_checklist"]["account"]["id"] == "acc_selected"
    monkeypatch.delenv("VF_DB_PATH", raising=False)


def test_publish_manager_rejects_cross_platform_account(monkeypatch, tmp_path):
    db_path = tmp_path / "video_factory.db"
    cookie_path = tmp_path / "cookie.json"
    cookie_path.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("VF_DB_PATH", str(db_path))
    Config.reset()
    db = Database(db_path=str(db_path))
    db.insert_account(
        {
            "id": "acc_xhs",
            "platform": "xiaohongshu",
            "name": "小红书账号",
            "cookie_path": str(cookie_path),
            "status": "active",
            "last_test": None,
            "created_at": "2026-03-09T14:32:00",
            "is_default": True,
            "capabilities": {
                "platform_supported": True,
                "cookie_required": True,
                "cookie_exists": True,
                "can_auto_publish": True,
                "can_manual_publish": True,
            },
            "last_error": "",
        }
    )

    manager = PublishManager()
    failed = asyncio.run(
        manager.publish_to_platform(
            platform="bilibili",
            video_path="/tmp/video.mp4",
            title="demo",
            task_id="vf_cross_platform",
            job_id="pubjob_cross_platform",
            account_id="acc_xhs",
        )
    )
    assert failed["success"] is False
    assert "不匹配" in failed["error"]
    monkeypatch.delenv("VF_DB_PATH", raising=False)
