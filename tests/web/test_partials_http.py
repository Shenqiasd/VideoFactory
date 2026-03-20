import pytest

from api.routes import tasks as tasks_routes
from core import task as task_module
from core.task import TaskState


@pytest.mark.parametrize(
    "path",
    [
        "/web/partials/stats_cards",
        "/web/partials/active_tasks",
        "/web/partials/service_status",
        "/web/partials/service_status_sidebar",
        "/web/partials/service_status_detail",
        "/web/partials/storage_overview",
        "/web/partials/task_list",
        "/web/partials/recent_completed",
    ],
)
def test_partials_return_200(client, path: str):
    response = client.get(path)

    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


def test_task_list_partial_accepts_status_filter(client):
    response = client.get("/web/partials/task_list", params={"status": "active"})

    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


def test_task_list_partial_exposes_delete_action(client):
    created = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=delete_case",
            "source_title": "delete-title",
        },
    )

    response = client.get("/web/partials/task_list")

    assert response.status_code == 200
    assert f'hx-delete="/api/tasks/{created.json()["task_id"]}"' in response.text
    assert "删除" in response.text


def test_task_list_active_filter_includes_downloaded_and_qc_passed(client):
    first = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=downloaded_case",
            "source_title": "downloaded-title",
        },
    )
    second = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=qc_case",
            "source_title": "qc-passed-title",
        },
    )

    store = tasks_routes.get_task_store()
    downloaded_task = store.get(first.json()["task_id"])
    downloaded_task.state = TaskState.DOWNLOADED.value
    store.update(downloaded_task)

    qc_task = store.get(second.json()["task_id"])
    qc_task.state = TaskState.QC_PASSED.value
    store.update(qc_task)

    response = client.get("/web/partials/task_list", params={"status": "active"})

    assert response.status_code == 200
    assert "downloaded-title" in response.text
    assert "qc-passed-title" in response.text


def test_active_and_task_list_partials_use_persisted_progress(client):
    created = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=progress_case",
            "source_title": "progress-title",
        },
    )
    task_id = created.json()["task_id"]

    store = tasks_routes.get_task_store()
    task = store.get(task_id)
    task.state = TaskState.TRANSLATING.value
    task.progress = 42
    store.update(task)

    active_response = client.get("/web/partials/active_tasks")
    list_response = client.get("/web/partials/task_list")

    assert active_response.status_code == 200
    assert list_response.status_code == 200
    assert "42%" in active_response.text
    assert 'style="width: 42%"' in list_response.text


def test_active_tasks_partial_prefers_project_name(client):
    created = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=active_title_case",
            "source_title": "active-title",
        },
    )
    task_id = created.json()["task_id"]

    store = tasks_routes.get_task_store()
    task = store.get(task_id)
    task.translated_title = "规范项目名"
    store.update(task)

    response = client.get("/web/partials/active_tasks")
    assert response.status_code == 200
    html = response.text
    assert "规范项目名" in html


def test_task_list_partial_derives_platforms_from_publish_accounts(client):
    created = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=platform_case",
            "source_title": "platform-title",
        },
    )
    task_id = created.json()["task_id"]

    store = tasks_routes.get_task_store()
    task = store.get(task_id)
    task.publish_accounts = {"youtube": "acct_youtube"}
    store.update(task)

    response = client.get("/web/partials/task_list", params={"platform": "youtube"})

    assert response.status_code == 200
    assert "platform-title" in response.text
    assert "youtube" in response.text


def test_task_list_partial_renders_cover_preview_and_summary(client, tmp_path):
    created = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=cover_summary_case",
            "source_title": "cover-summary-title",
        },
    )
    task_id = created.json()["task_id"]

    cover_file = tmp_path / "cover.png"
    long_video = tmp_path / "long_video.mp4"
    cover_file.write_bytes(b"fake-cover")
    long_video.write_bytes(b"fake-video")

    store = tasks_routes.get_task_store()
    task = store.get(task_id)
    task.translated_title = "字幕任务项目名"
    task.products = [
        {
            "type": "long_video",
            "platform": "all",
            "local_path": str(long_video),
            "title": "字幕任务项目名",
            "description": "这是任务列表里展示的视频简介，长度控制在两百字以内。",
        },
        {
            "type": "cover",
            "platform": "all",
            "local_path": str(cover_file),
            "metadata": {"cover_type": "horizontal"},
        },
    ]
    store.update(task)

    response = client.get("/web/partials/task_list")

    assert response.status_code == 200
    assert "这是任务列表里展示的视频简介" in response.text
    assert f"/api/tasks/{task_id}/artifacts/{tasks_routes._make_artifact_id(str(cover_file))}/download?inline=1" in response.text
    assert "<img" in response.text


def test_recent_completed_partial_supports_float_timestamps_and_products(client, monkeypatch):
    created = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=completed_case",
            "source_title": "completed-title",
        },
    )
    task_id = created.json()["task_id"]

    store = tasks_routes.get_task_store()
    task = store.get(task_id)
    task.state = TaskState.COMPLETED.value
    task.created_at = 1710000000.0
    task.translated_title = "完成项目名"
    task.products = [{"type": "long_video", "platform": "all", "title": "completed-title"}]
    monkeypatch.setattr(task_module.time, "time", lambda: 1710003661.0)
    store.update(task)

    response = client.get("/web/partials/recent_completed")

    assert response.status_code == 200
    assert "完成项目名" in response.text
    assert "1小时1分钟" in response.text
    assert "长视频" in response.text


def test_partials_do_not_emit_template_response_deprecation(client, recwarn):
    response = client.get("/web/partials/task_list")

    assert response.status_code == 200
    assert not [
        warning
        for warning in recwarn
        if "name is not the first parameter anymore" in str(warning.message)
    ]
