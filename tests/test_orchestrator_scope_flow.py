import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.task import Task, TaskState  # noqa: E402
from workers.orchestrator import Orchestrator  # noqa: E402


class DummyTaskStore:
    def __init__(self):
        self._tasks = {}
        self.updated_states = []

    def list_by_state(self, state):
        return []

    def update(self, task):
        self.updated_states.append(task.state)


@pytest.mark.asyncio
async def test_subtitle_only_runs_factory_then_completes():
    store = DummyTaskStore()
    production = SimpleNamespace(run=AsyncMock(return_value=True), close=AsyncMock())

    async def fake_factory_run(task):
        task.transition(TaskState.PROCESSING)
        task.transition(TaskState.UPLOADING_PRODUCTS)
        task.transition(TaskState.READY_TO_PUBLISH)
        return True

    factory = SimpleNamespace(run=AsyncMock(side_effect=fake_factory_run), close=AsyncMock())
    scheduler = SimpleNamespace(schedule_staggered=lambda *args, **kwargs: None)
    notifier = SimpleNamespace(notify_error=AsyncMock(), close=AsyncMock())

    orchestrator = Orchestrator(
        task_store=store,
        production=production,
        factory=factory,
        scheduler=scheduler,
        notifier=notifier,
    )

    task = Task(
        source_url="https://example.com/video",
        state=TaskState.QC_PASSED.value,
        task_scope="subtitle_only",
        enable_tts=False,
    )

    success = await orchestrator.process_task(task)

    assert success is True
    assert factory.run.await_count == 1
    assert task.state == TaskState.COMPLETED.value


@pytest.mark.asyncio
async def test_subtitle_dub_completes_without_factory():
    store = DummyTaskStore()
    production = SimpleNamespace(run=AsyncMock(return_value=True), close=AsyncMock())
    factory = SimpleNamespace(run=AsyncMock(return_value=True), close=AsyncMock())
    scheduler = SimpleNamespace(schedule_staggered=lambda *args, **kwargs: None)
    notifier = SimpleNamespace(notify_error=AsyncMock(), close=AsyncMock())

    orchestrator = Orchestrator(
        task_store=store,
        production=production,
        factory=factory,
        scheduler=scheduler,
        notifier=notifier,
    )

    task = Task(
        source_url="https://example.com/video",
        state=TaskState.QC_PASSED.value,
        task_scope="subtitle_dub",
    )

    success = await orchestrator.process_task(task)

    assert success is True
    assert factory.run.await_count == 0
    assert task.state == TaskState.COMPLETED.value


@pytest.mark.asyncio
async def test_full_scope_waits_for_creation_review_before_publishing():
    store = DummyTaskStore()
    production = SimpleNamespace(run=AsyncMock(return_value=True), close=AsyncMock())
    factory = SimpleNamespace(run=AsyncMock(return_value=True), close=AsyncMock())
    scheduler = SimpleNamespace(schedule_staggered=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("should not publish")))
    notifier = SimpleNamespace(notify_error=AsyncMock(), close=AsyncMock())

    orchestrator = Orchestrator(
        task_store=store,
        production=production,
        factory=factory,
        scheduler=scheduler,
        notifier=notifier,
    )

    task = Task(
        source_url="https://example.com/video",
        state=TaskState.READY_TO_PUBLISH.value,
        task_scope="full",
        creation_status={
            "review_required": True,
            "review_status": "pending",
        },
    )

    success = await orchestrator.process_task(task)

    assert success is True
    assert task.state == TaskState.READY_TO_PUBLISH.value
