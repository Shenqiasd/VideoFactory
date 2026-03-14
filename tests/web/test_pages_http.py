import pytest


@pytest.mark.parametrize(
    ("path", "marker"),
    [
        ("/", "系统运行状态一览"),
        ("/tasks/new", "创建新任务"),
        ("/tasks", "任务列表"),
        ("/tasks/vf_demo_001", "任务详情"),
        ("/publish", "发布管理"),
        ("/storage", "存储管理"),
        ("/settings", "设置"),
    ],
)
def test_pages_return_200(client, path: str, marker: str):
    response = client.get(path)

    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert marker in response.text


def test_pages_do_not_emit_template_response_deprecation(client, recwarn):
    response = client.get("/")

    assert response.status_code == 200
    assert not [
        warning
        for warning in recwarn
        if "name is not the first parameter anymore" in str(warning.message)
    ]


def test_task_detail_page_contains_description_copy_controls(client):
    response = client.get("/tasks/vf_demo_001")

    assert response.status_code == 200
    assert 'id="task-translated-description"' in response.text
    assert 'id="copy-description-button"' in response.text

def test_new_task_page_contains_creation_config_panel(client):
    response = client.get('/tasks/new')

    assert response.status_code == 200
    assert '创作配置' in response.text
    assert 'name="creation_clip_count"' in response.text
    assert 'name="creation_duration_min"' in response.text
    assert 'name="creation_duration_max"' in response.text
    assert 'name="creation_crop_mode"' in response.text
    assert 'name="creation_review_mode"' in response.text
    assert 'name="creation_platforms"' in response.text
    assert 'name="creation_bgm_path"' in response.text
    assert 'name="creation_intro_path"' in response.text
    assert 'name="creation_outro_path"' in response.text
    assert 'name="creation_transition"' in response.text
    assert '本次创作将生成' in response.text


def test_tasks_page_renders_task_list_without_client_side_fetch(client):
    created = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=tasks_page_case",
            "source_title": "tasks-page-title",
        },
    )
    assert created.status_code == 200

    response = client.get("/tasks")

    assert response.status_code == 200
    assert 'id="task-list-container"' in response.text
    assert 'tasks-page-title' in response.text
    assert '/tasks?status=active' in response.text
    assert '/tasks?status=completed' in response.text


def test_tasks_page_status_filter_works_without_htmx(client):
    failed = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=tasks_failed_case",
            "source_title": "failed-only-title",
        },
    )
    active = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=tasks_active_case",
            "source_title": "active-only-title",
        },
    )
    assert failed.status_code == 200 and active.status_code == 200

    from api.routes import tasks as tasks_routes
    from core.task import TaskState

    store = tasks_routes.get_task_store()
    failed_task = store.get(failed.json()["task_id"])
    failed_task.state = TaskState.FAILED.value
    store.update(failed_task)

    active_task = store.get(active.json()["task_id"])
    active_task.state = TaskState.TRANSLATING.value
    store.update(active_task)

    response = client.get("/tasks", params={"status": "failed"})

    assert response.status_code == 200
    assert 'failed-only-title' in response.text
    assert 'active-only-title' not in response.text



def test_task_detail_page_contains_creation_review_panel(client):
    response = client.get("/tasks/vf_demo_001")

    assert response.status_code == 200
    assert 'id="creation-summary"' in response.text
    assert 'id="creation-review-actions"' in response.text
    assert 'id="creation-reject-modal"' in response.text
    assert 'id="creation-reject-note"' in response.text
    assert 'id="confirm-reject-modal-button"' in response.text
    assert '创作结果' in response.text
    assert '创作审核' in response.text


def test_tasks_page_renders_creation_badges(client):
    from api.routes import tasks as tasks_routes

    created = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=creation_badge_case",
            "source_title": "creation-badge-title",
        },
    )
    assert created.status_code == 200

    store = tasks_routes.get_task_store()
    task = store.get(created.json()["task_id"])
    task.products = [
        {
            "type": "short_clip",
            "platform": "douyin",
            "local_path": "/tmp/clip.mp4",
            "title": "clip",
            "metadata": {"segment_id": "seg_001", "review_status": "pending"},
        },
        {
            "type": "cover",
            "platform": "all",
            "local_path": "/tmp/cover.jpg",
            "title": "cover",
            "metadata": {"cover_type": "horizontal"},
        },
    ]
    task.creation_status = {"review_required": True, "review_status": "pending"}
    store.update(task)

    response = client.get("/tasks")

    assert response.status_code == 200
    assert 'creation-badge-title' in response.text
    assert '待审核' in response.text
    assert '1切片' in response.text
    assert '已出封面' in response.text


def test_dashboard_renders_stats_and_active_tasks_without_client_side_fetch(client):
    response = client.get("/")

    assert response.status_code == 200
    assert 'id="dashboard-stats"' in response.text
    assert 'id="dashboard-active-tasks"' in response.text
    assert '总任务' in response.text
    assert '活跃任务' in response.text
    assert '最后检查' in response.text
    assert 'animate-pulse' not in response.text
