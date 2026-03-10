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
