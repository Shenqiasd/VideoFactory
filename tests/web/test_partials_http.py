import pytest


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
