import asyncio
import sys
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.notification import NotificationManager  # noqa: E402
from core.task import Task, TaskStore, TaskState  # noqa: E402
from distribute.scheduler import PublishScheduler  # noqa: E402


class DummyPublishManager:
    def __init__(self, success: bool):
        self.success = success

    async def publish_to_platform(self, **kwargs):
        if self.success:
            return {"success": True, "url": "https://example.com/video/1", "error": ""}
        return {"success": False, "url": "", "error": "mock failure"}


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

