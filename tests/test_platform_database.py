"""
Sprint 1: 平台相关数据库 CRUD 单元测试。

使用临时内存数据库隔离测试。
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.database import Database  # noqa: E402


@pytest.fixture
def db(tmp_path):
    """创建临时数据库实例。"""
    db_path = str(tmp_path / "test.db")
    return Database(db_path=db_path)


# ---------------------------------------------------------------------------
# platform_accounts
# ---------------------------------------------------------------------------

class TestPlatformAccounts:
    def test_insert_and_get(self, db):
        db.insert_platform_account({
            "id": "pa_1",
            "platform": "youtube",
            "auth_method": "oauth2",
            "platform_uid": "UC123",
            "username": "testuser",
            "nickname": "Test User",
            "avatar_url": "https://example.com/avatar.png",
            "status": "active",
        })
        acc = db.get_platform_account("pa_1")
        assert acc is not None
        assert acc["platform"] == "youtube"
        assert acc["nickname"] == "Test User"
        assert acc["platform_uid"] == "UC123"

    def test_get_by_uid(self, db):
        db.insert_platform_account({
            "id": "pa_1",
            "platform": "youtube",
            "platform_uid": "UC123",
            "nickname": "Test",
        })
        acc = db.get_platform_account_by_uid("youtube", "UC123")
        assert acc is not None
        assert acc["id"] == "pa_1"

        assert db.get_platform_account_by_uid("youtube", "nonexistent") is None

    def test_list_all(self, db):
        db.insert_platform_account({"id": "pa_1", "platform": "youtube", "nickname": "YT"})
        db.insert_platform_account({"id": "pa_2", "platform": "bilibili", "nickname": "BL"})
        all_accounts = db.get_platform_accounts()
        assert len(all_accounts) == 2

    def test_list_by_platform(self, db):
        db.insert_platform_account({"id": "pa_1", "platform": "youtube", "nickname": "YT"})
        db.insert_platform_account({"id": "pa_2", "platform": "bilibili", "nickname": "BL"})
        yt_accounts = db.get_platform_accounts(platform="youtube")
        assert len(yt_accounts) == 1
        assert yt_accounts[0]["platform"] == "youtube"

    def test_update(self, db):
        db.insert_platform_account({"id": "pa_1", "platform": "youtube", "nickname": "Old"})
        db.update_platform_account("pa_1", nickname="New", status="expired")
        acc = db.get_platform_account("pa_1")
        assert acc["nickname"] == "New"
        assert acc["status"] == "expired"

    def test_update_ignores_disallowed_fields(self, db):
        db.insert_platform_account({"id": "pa_1", "platform": "youtube", "nickname": "Test"})
        db.update_platform_account("pa_1", platform="bilibili")  # not in allowed set
        acc = db.get_platform_account("pa_1")
        assert acc["platform"] == "youtube"  # unchanged

    def test_delete(self, db):
        db.insert_platform_account({"id": "pa_1", "platform": "youtube", "nickname": "Test"})
        db.delete_platform_account("pa_1")
        assert db.get_platform_account("pa_1") is None

    def test_unique_platform_uid(self, db):
        db.insert_platform_account({"id": "pa_1", "platform": "youtube", "platform_uid": "UC123", "nickname": "A"})
        with pytest.raises(Exception):  # sqlite3.IntegrityError
            db.insert_platform_account({"id": "pa_2", "platform": "youtube", "platform_uid": "UC123", "nickname": "B"})


# ---------------------------------------------------------------------------
# oauth_credentials
# ---------------------------------------------------------------------------

class TestOAuthCredentials:
    def test_upsert_and_get(self, db):
        db.insert_platform_account({"id": "pa_1", "platform": "youtube", "nickname": "Test"})
        db.upsert_oauth_credential(
            account_id="pa_1",
            platform="youtube",
            access_token="at_123",
            refresh_token="rt_456",
            expires_at=1700000000,
        )
        cred = db.get_oauth_credential("pa_1")
        assert cred is not None
        assert cred["access_token"] == "at_123"
        assert cred["refresh_token"] == "rt_456"
        assert cred["expires_at"] == 1700000000

    def test_upsert_updates_existing(self, db):
        db.insert_platform_account({"id": "pa_1", "platform": "youtube", "nickname": "Test"})
        db.upsert_oauth_credential(
            account_id="pa_1", platform="youtube",
            access_token="old", refresh_token="rt", expires_at=100,
        )
        db.upsert_oauth_credential(
            account_id="pa_1", platform="youtube",
            access_token="new", refresh_token="rt2", expires_at=200,
        )
        cred = db.get_oauth_credential("pa_1")
        assert cred["access_token"] == "new"
        assert cred["refresh_token"] == "rt2"
        assert cred["expires_at"] == 200

    def test_delete(self, db):
        db.insert_platform_account({"id": "pa_1", "platform": "youtube", "nickname": "Test"})
        db.upsert_oauth_credential(
            account_id="pa_1", platform="youtube",
            access_token="at", refresh_token="rt", expires_at=100,
        )
        db.delete_oauth_credential("pa_1")
        assert db.get_oauth_credential("pa_1") is None

    def test_get_nonexistent(self, db):
        assert db.get_oauth_credential("nonexistent") is None


# ---------------------------------------------------------------------------
# publish_tasks_v2
# ---------------------------------------------------------------------------

class TestPublishTasksV2:
    def test_insert_and_get(self, db):
        db.insert_platform_account({"id": "pa_1", "platform": "youtube", "nickname": "Test"})
        db.insert_publish_task_v2({
            "id": "pt_1",
            "account_id": "pa_1",
            "platform": "youtube",
            "title": "Test Video",
            "description": "A test video",
            "tags": ["tag1", "tag2"],
            "video_path": "/tmp/video.mp4",
            "status": "pending",
        })
        task = db.get_publish_task_v2("pt_1")
        assert task is not None
        assert task["title"] == "Test Video"
        assert task["tags"] == ["tag1", "tag2"]
        assert task["platform_options"] == {}
        assert task["status"] == "pending"

    def test_list_with_filters(self, db):
        db.insert_platform_account({"id": "pa_1", "platform": "youtube", "nickname": "YT"})
        db.insert_platform_account({"id": "pa_2", "platform": "bilibili", "nickname": "BL"})
        db.insert_publish_task_v2({"id": "pt_1", "account_id": "pa_1", "platform": "youtube", "title": "V1", "status": "pending"})
        db.insert_publish_task_v2({"id": "pt_2", "account_id": "pa_2", "platform": "bilibili", "title": "V2", "status": "published"})
        db.insert_publish_task_v2({"id": "pt_3", "account_id": "pa_1", "platform": "youtube", "title": "V3", "status": "pending"})

        # 按平台过滤
        yt_tasks = db.get_publish_tasks_v2(platform="youtube")
        assert len(yt_tasks) == 2

        # 按状态过滤
        pending = db.get_publish_tasks_v2(status="pending")
        assert len(pending) == 2

        # 按账号过滤
        pa1_tasks = db.get_publish_tasks_v2(account_id="pa_1")
        assert len(pa1_tasks) == 2

        # 组合过滤
        combo = db.get_publish_tasks_v2(platform="youtube", status="pending")
        assert len(combo) == 2

    def test_update(self, db):
        db.insert_platform_account({"id": "pa_1", "platform": "youtube", "nickname": "Test"})
        db.insert_publish_task_v2({"id": "pt_1", "account_id": "pa_1", "platform": "youtube", "title": "V1", "status": "pending"})

        db.update_publish_task_v2("pt_1", status="published", post_id="yt_123", permalink="https://youtube.com/watch?v=123")
        task = db.get_publish_task_v2("pt_1")
        assert task["status"] == "published"
        assert task["post_id"] == "yt_123"
        assert task["permalink"] == "https://youtube.com/watch?v=123"

    def test_update_ignores_disallowed(self, db):
        db.insert_platform_account({"id": "pa_1", "platform": "youtube", "nickname": "Test"})
        db.insert_publish_task_v2({"id": "pt_1", "account_id": "pa_1", "platform": "youtube", "title": "V1", "status": "pending"})
        db.update_publish_task_v2("pt_1", title="Changed")  # title not in allowed
        task = db.get_publish_task_v2("pt_1")
        assert task["title"] == "V1"  # unchanged

    def test_delete(self, db):
        db.insert_platform_account({"id": "pa_1", "platform": "youtube", "nickname": "Test"})
        db.insert_publish_task_v2({"id": "pt_1", "account_id": "pa_1", "platform": "youtube", "title": "V1"})
        db.delete_publish_task_v2("pt_1")
        assert db.get_publish_task_v2("pt_1") is None

    def test_limit(self, db):
        db.insert_platform_account({"id": "pa_1", "platform": "youtube", "nickname": "Test"})
        for i in range(5):
            db.insert_publish_task_v2({"id": f"pt_{i}", "account_id": "pa_1", "platform": "youtube", "title": f"V{i}"})
        tasks = db.get_publish_tasks_v2(limit=3)
        assert len(tasks) == 3
