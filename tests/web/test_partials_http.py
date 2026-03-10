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


def test_active_tasks_partial_renders_task_source_title_or_url(client):
    client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=active_title_case",
            "source_title": "active-title",
        },
    )

    response = client.get("/web/partials/active_tasks")
    assert response.status_code == 200
    html = response.text
    assert "active-title" in html or "active_title_case" in html


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
    task.products = [{"type": "long_video", "platform": "all", "title": "completed-title"}]
    monkeypatch.setattr(task_module.time, "time", lambda: 1710003661.0)
    store.update(task)

    response = client.get("/web/partials/recent_completed")

    assert response.status_code == 200
    assert "completed-title" in response.text
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
