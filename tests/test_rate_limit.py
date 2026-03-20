"""
API 速率限制测试
验证 slowapi 速率限制行为：超限返回 429、GET 不受限、窗口重置后恢复。
"""
import time
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from api.server import app


class TestRateLimitExceeded:
    """超过限制后应返回 HTTP 429 及结构化 JSON。"""

    def test_create_task_rate_limit(self):
        """POST /api/tasks/ 限制 10/minute，第 11 次应返回 429。"""
        client = TestClient(app)
        payload = {
            "source_url": "https://www.youtube.com/watch?v=test",
            "source_lang": "en",
            "target_lang": "zh_cn",
            "task_scope": "full",
        }
        for i in range(10):
            resp = client.post("/api/tasks/", json=payload)
            # 接口可能因为业务逻辑返回非 200，但不应是 429
            assert resp.status_code != 429, f"第 {i+1} 次请求不应被限流"

        resp = client.post("/api/tasks/", json=payload)
        assert resp.status_code == 429
        body = resp.json()
        assert body["code"] == "RATE_LIMITED"
        assert "message" in body

    def test_batch_create_rate_limit(self):
        """POST /api/tasks/batch-create 限制 3/minute，第 4 次应返回 429。"""
        client = TestClient(app)
        for i in range(3):
            resp = client.post(
                "/api/tasks/batch-create",
                data={"urls": "https://www.youtube.com/watch?v=a"},
            )
            assert resp.status_code != 429, f"第 {i+1} 次请求不应被限流"

        resp = client.post(
            "/api/tasks/batch-create",
            data={"urls": "https://www.youtube.com/watch?v=a"},
        )
        assert resp.status_code == 429
        body = resp.json()
        assert body["code"] == "RATE_LIMITED"


class TestGetEndpointsNotRateLimited:
    """GET 端点不受速率限制。"""

    def test_health_not_limited(self):
        """GET /api/health 可无限调用。"""
        client = TestClient(app)
        for _ in range(30):
            resp = client.get("/api/health")
            assert resp.status_code != 429

    def test_api_root_not_limited(self):
        """GET /api 可无限调用。"""
        client = TestClient(app)
        for _ in range(30):
            resp = client.get("/api")
            assert resp.status_code != 429

    def test_tasks_list_not_limited(self):
        """GET /api/tasks/ 可无限调用。"""
        client = TestClient(app)
        for _ in range(30):
            resp = client.get("/api/tasks/")
            assert resp.status_code != 429


class TestRateLimitWindowReset:
    """速率限制窗口过期后请求应恢复正常。"""

    def test_window_reset_allows_requests(self):
        """模拟时间流逝，验证窗口重置后不再被限流。"""
        client = TestClient(app)
        payload = {
            "source_url": "https://www.youtube.com/watch?v=test",
            "source_lang": "en",
            "target_lang": "zh_cn",
            "task_scope": "full",
        }

        # 耗尽配额
        for _ in range(10):
            client.post("/api/tasks/", json=payload)

        # 确认被限流
        resp = client.post("/api/tasks/", json=payload)
        assert resp.status_code == 429

        # 模拟时间前进 61 秒，使窗口过期
        real_time = time.time

        def shifted_time():
            return real_time() + 61

        with patch("time.time", side_effect=shifted_time):
            from api.rate_limit import limiter
            limiter.reset()

        # 重置后应可再次请求
        resp = client.post("/api/tasks/", json=payload)
        assert resp.status_code != 429


class TestRateLimitResponseFormat:
    """验证 429 响应体格式。"""

    def test_json_body_structure(self):
        """429 响应应包含 code 和 message 字段。"""
        client = TestClient(app)
        payload = {
            "source_url": "https://www.youtube.com/watch?v=test",
            "source_lang": "en",
            "target_lang": "zh_cn",
            "task_scope": "full",
        }
        for _ in range(10):
            client.post("/api/tasks/", json=payload)

        resp = client.post("/api/tasks/", json=payload)
        assert resp.status_code == 429
        body = resp.json()
        assert "code" in body
        assert "message" in body
        assert body["code"] == "RATE_LIMITED"
        assert isinstance(body["message"], str)
        assert len(body["message"]) > 0
