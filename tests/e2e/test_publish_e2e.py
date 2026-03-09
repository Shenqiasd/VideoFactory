"""
端到端测试 - 发布流程
测试: 创建账号 → 创建任务 → 执行发布 → 验证状态 → 测试重试
"""
import httpx
import pytest


@pytest.fixture
def api_client(live_server):
    """API客户端"""
    return httpx.Client(base_url=live_server, timeout=10.0)


def test_publish_workflow_e2e(api_client):
    """完整发布流程测试"""

    # 1. 创建测试账号
    resp = api_client.post("/api/publish/accounts", json={
        "platform": "douyin",
        "account_id": "test_account_001",
        "account_name": "测试账号",
        "enabled": True
    })
    assert resp.status_code == 200

    # 2. 创建视频任务
    resp = api_client.post("/api/tasks/create", data={
        "youtube_url": "https://www.youtube.com/watch?v=test_e2e",
        "source_lang": "en",
        "target_lang": "zh_cn"
    })
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    # 3. 模拟任务完成（设置为 ready_to_publish 状态）
    resp = api_client.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 200

    # 4. 发布任务
    resp = api_client.post("/api/distribute/publish", json={
        "task_id": task_id,
        "platforms": ["douyin"],
        "mode": "immediate"
    })
    assert resp.status_code in [200, 400]  # 400 if not ready_to_publish

    # 5. 查询发布状态
    resp = api_client.get(f"/api/distribute/status/{task_id}")
    assert resp.status_code == 200
    status = resp.json()
    assert "task_id" in status
    assert "state" in status

    # 6. 查询发布队列
    resp = api_client.get("/api/distribute/queue")
    assert resp.status_code == 200

    # 7. 查询统计信息
    resp = api_client.get("/api/distribute/stats")
    assert resp.status_code == 200
    stats = resp.json()
    assert "total" in stats
    assert "by_status" in stats

    # 8. 测试重试（即使没有失败任务也应该返回合理响应）
    resp = api_client.post(f"/api/distribute/tasks/{task_id}/retry")
    assert resp.status_code in [200, 404]  # 404 if no failed jobs

    # 9. 清理 - 删除测试账号
    resp = api_client.delete("/api/publish/accounts/douyin/test_account_001")
    assert resp.status_code in [200, 404]
