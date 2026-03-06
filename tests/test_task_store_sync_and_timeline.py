import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.task import Task, TaskState, TaskStore  # noqa: E402


def test_task_records_step_and_state_timeline_events():
    task = Task(source_url="https://example.com/video")
    initial_events = len(task.timeline)

    task.mark_step(TaskState.DOWNLOADING.value)
    transitioned = task.transition(TaskState.DOWNLOADING)

    assert transitioned is True
    assert len(task.timeline) >= initial_events + 2

    events = [event["event"] for event in task.timeline]
    assert "step_transition" in events
    assert "state_transition" in events

    latest_state = [event for event in task.timeline if event["event"] == "state_transition"][-1]
    assert latest_state["from_state"] == TaskState.QUEUED.value
    assert latest_state["to_state"] == TaskState.DOWNLOADING.value


def test_task_store_reload_replaces_removed_tasks(tmp_path):
    store_path = tmp_path / "tasks.json"
    store = TaskStore(store_path=str(store_path))

    removed_task = store.create(source_url="https://example.com/old")
    kept_task = Task(source_url="https://example.com/new")

    store_path.write_text(
        json.dumps({kept_task.task_id: kept_task.to_dict()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    store._load()  # noqa: SLF001 - 测试跨进程重载行为

    assert store.get(removed_task.task_id) is None
    assert store.get(kept_task.task_id) is not None
    assert len(store.list_all()) == 1


def test_task_store_auto_refresh_between_processes(tmp_path):
    store_path = tmp_path / "tasks.json"
    store_a = TaskStore(store_path=str(store_path))
    store_b = TaskStore(store_path=str(store_path))

    task = store_a.create(source_url="https://example.com/sync")
    assert store_b.get(task.task_id) is not None

    task_a = store_a.get(task.task_id)
    assert task_a is not None
    task_a.transition(TaskState.FAILED)
    store_a.update(task_a)

    task_b = store_b.get(task.task_id)
    assert task_b is not None
    assert task_b.state == TaskState.FAILED.value
