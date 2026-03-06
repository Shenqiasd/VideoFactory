import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.task import TaskStore  # noqa: E402
from production.pipeline import ProductionPipeline  # noqa: E402


class AlwaysUnavailableKlicClient:
    async def get_task_status(self, task_id: str):
        return None


class FailedKlicClient:
    async def get_task_status(self, task_id: str):
        return {
            "status": 3,
            "process_percent": 42,
            "error_msg": "simulated failure",
        }


@pytest.mark.asyncio
async def test_poll_klic_progress_returns_status_unavailable(monkeypatch, tmp_path):
    async def _fast_sleep(seconds: float):
        return None

    monkeypatch.setattr("production.pipeline.asyncio.sleep", _fast_sleep)

    store = TaskStore(store_path=str(tmp_path / "tasks.json"))
    task = store.create(source_url="https://example.com/video")
    task.klic_task_id = "klic_unavailable"

    pipeline = ProductionPipeline(task_store=store, klic_client=AlwaysUnavailableKlicClient())

    result, error_code = await pipeline._poll_klic_progress(task)

    assert result is None
    assert error_code == "KLIC_STATUS_UNAVAILABLE"


@pytest.mark.asyncio
async def test_poll_klic_progress_returns_task_failed(tmp_path):
    store = TaskStore(store_path=str(tmp_path / "tasks.json"))
    task = store.create(source_url="https://example.com/video")
    task.klic_task_id = "klic_failed"

    pipeline = ProductionPipeline(task_store=store, klic_client=FailedKlicClient())

    result, error_code = await pipeline._poll_klic_progress(task)

    assert result is None
    assert error_code == "KLIC_TASK_FAILED"
    assert task.klic_progress == 42
    assert task.progress == 30 + int(42 * 0.4)
