import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.task import TaskState, TaskStore  # noqa: E402
from production.pipeline import ProductionPipeline  # noqa: E402


class NoopNotifier:
    async def notify_task_state_change(self, *args, **kwargs):
        return None

    async def notify(self, *args, **kwargs):
        return None

    async def notify_error(self, *args, **kwargs):
        return None

    async def close(self):
        return None


class FlakyKlicClient:
    def __init__(self):
        self.calls = 0
        self.last_error = ""

    async def submit_task(self, **kwargs):
        self.calls += 1
        if self.calls < 3:
            self.last_error = "All connection attempts failed"
            return None
        return "klic_task_ok"


class AlwaysUnavailableSubmitClient:
    def __init__(self):
        self.last_error = "All connection attempts failed"

    async def submit_task(self, **kwargs):
        return None


@pytest.mark.asyncio
async def test_submit_klic_task_with_retry_eventually_succeeds(monkeypatch, tmp_path):
    async def _fast_sleep(seconds: float):
        return None

    monkeypatch.setattr("production.pipeline.asyncio.sleep", _fast_sleep)

    store = TaskStore(store_path=str(tmp_path / "tasks.json"))
    task = store.create(source_url="https://example.com/video")
    client = FlakyKlicClient()
    pipeline = ProductionPipeline(task_store=store, klic_client=client, notifier=NoopNotifier())

    task_id, error = await pipeline._submit_klic_task_with_retry(task, "local:/tmp/source.mp4")

    assert task_id == "klic_task_ok"
    assert error == ""
    assert client.calls == 3


@pytest.mark.asyncio
async def test_step_translate_marks_klic_unavailable_when_submit_connection_fails(monkeypatch, tmp_path):
    async def _fast_sleep(seconds: float):
        return None

    monkeypatch.setattr("production.pipeline.asyncio.sleep", _fast_sleep)

    source_file = tmp_path / "source_video.mp4"
    source_file.write_bytes(b"x" * 2048)

    store = TaskStore(store_path=str(tmp_path / "tasks.json"))
    task = store.create(source_url="https://example.com/video")
    task.source_local_path = str(source_file)
    task.state = TaskState.UPLOADING_SOURCE.value
    store.update(task)

    pipeline = ProductionPipeline(
        task_store=store,
        klic_client=AlwaysUnavailableSubmitClient(),
        notifier=NoopNotifier(),
    )

    ok = await pipeline._step_translate(task, tmp_path)

    assert ok is False
    assert task.state == TaskState.FAILED.value
    assert task.last_error_code == "KLIC_UNAVAILABLE"
    assert "KlicStudio 服务不可用" in task.error_message
