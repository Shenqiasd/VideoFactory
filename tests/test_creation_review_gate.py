import sys
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from api.server import app  # noqa: E402
from api.routes import distribute as distribute_routes  # noqa: E402
from core.task import Task, TaskState  # noqa: E402


def test_publish_requires_creation_review_approval():
    client = TestClient(app)

    task = Task(task_id="vf_creation_review_gate", source_url="https://example.com", source_title="demo")
    task.state = TaskState.READY_TO_PUBLISH.value
    task.products = [
        {
            "type": "short_clip",
            "platform": "douyin",
            "local_path": "/tmp/video.mp4",
            "title": "title",
        }
    ]
    task.creation_status = {
        "review_required": True,
        "review_status": "pending",
    }
    distribute_routes.get_task_store().update(task)

    response = client.post(
        "/api/distribute/publish",
        json={
            "task_id": task.task_id,
            "platforms": ["douyin"],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "CREATION_REVIEW_PENDING"


def test_publish_allows_long_video_targets_when_only_short_clip_is_pending_review():
    client = TestClient(app)

    task = Task(task_id="vf_creation_review_long_video_ok", source_url="https://example.com", source_title="demo")
    task.state = TaskState.READY_TO_PUBLISH.value
    task.products = [
        {
            "type": "long_video",
            "platform": "all",
            "local_path": "/tmp/long.mp4",
            "title": "long",
        },
        {
            "type": "short_clip",
            "platform": "douyin",
            "local_path": "/tmp/short.mp4",
            "title": "short",
        },
    ]
    task.creation_status = {
        "review_required": True,
        "review_status": "pending",
    }
    distribute_routes.get_task_store().update(task)

    response = client.post(
        "/api/distribute/publish",
        json={
            "task_id": task.task_id,
            "platforms": ["youtube"],
        },
    )

    assert response.status_code == 200
