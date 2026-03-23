"""
PublishTemplateService + Database CRUD 单元测试。

覆盖：
- 模板 CRUD（create / list / get / update / delete）
- apply_template 生成正确的任务规格
- 模板变量替换
- 边界情况（空平台、不存在的模板、空字段）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import json
import os
import tempfile

import pytest

from core.database import Database
from platform_services.templates import PublishTemplateService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """Create a fresh in-memory-like DB for each test."""
    db_path = str(tmp_path / "test_templates.db")
    database = Database(db_path=db_path)
    yield database
    database.close()


@pytest.fixture
def service(db):
    return PublishTemplateService(db)


# ---------------------------------------------------------------------------
# Database-level CRUD tests
# ---------------------------------------------------------------------------


class TestDatabaseTemplateCRUD:
    """Test the Database publish_templates methods directly."""

    def test_insert_and_get_template(self, db):
        """insert_publish_template + get_publish_template round-trip."""
        template = {
            "id": "tmpl-001",
            "user_id": "user-1",
            "name": "My Template",
            "platforms": '["youtube", "bilibili"]',
            "title_template": "Test Title",
            "description_template": "Test Desc",
            "tags": '["tag1"]',
            "platform_options": '{}',
        }
        db.insert_publish_template(template)
        result = db.get_publish_template("tmpl-001")
        assert result is not None
        assert result["name"] == "My Template"
        assert result["user_id"] == "user-1"
        assert result["platforms"] == '["youtube", "bilibili"]'

    def test_get_nonexistent_template(self, db):
        """get_publish_template returns None for missing id."""
        assert db.get_publish_template("nonexistent") is None

    def test_list_templates_all(self, db):
        """get_publish_templates returns all templates."""
        for i in range(3):
            db.insert_publish_template({
                "id": f"tmpl-{i}",
                "user_id": "user-1",
                "name": f"Template {i}",
            })
        templates = db.get_publish_templates()
        assert len(templates) == 3

    def test_list_templates_by_user(self, db):
        """get_publish_templates filters by user_id."""
        db.insert_publish_template({"id": "t1", "user_id": "alice", "name": "A"})
        db.insert_publish_template({"id": "t2", "user_id": "bob", "name": "B"})
        db.insert_publish_template({"id": "t3", "user_id": "alice", "name": "C"})

        alice_templates = db.get_publish_templates(user_id="alice")
        assert len(alice_templates) == 2

        bob_templates = db.get_publish_templates(user_id="bob")
        assert len(bob_templates) == 1

    def test_update_template(self, db):
        """update_publish_template changes allowed fields."""
        db.insert_publish_template({
            "id": "tmpl-u",
            "user_id": "",
            "name": "Old Name",
            "platforms": "[]",
        })
        ok = db.update_publish_template("tmpl-u", name="New Name")
        assert ok is True
        updated = db.get_publish_template("tmpl-u")
        assert updated["name"] == "New Name"

    def test_update_template_nonexistent(self, db):
        """update_publish_template returns False for missing id."""
        ok = db.update_publish_template("missing", name="X")
        assert ok is False

    def test_update_template_no_fields(self, db):
        """update_publish_template returns False when no fields given."""
        ok = db.update_publish_template("any-id")
        assert ok is False

    def test_update_template_disallowed_field(self, db):
        """update_publish_template ignores disallowed fields."""
        db.insert_publish_template({"id": "tmpl-d", "user_id": "", "name": "T"})
        ok = db.update_publish_template("tmpl-d", user_id="hacker")
        assert ok is False  # user_id is not in allowed set

    def test_delete_template(self, db):
        """delete_publish_template removes a template."""
        db.insert_publish_template({"id": "tmpl-del", "user_id": "", "name": "Delete Me"})
        ok = db.delete_publish_template("tmpl-del")
        assert ok is True
        assert db.get_publish_template("tmpl-del") is None

    def test_delete_template_nonexistent(self, db):
        """delete_publish_template returns False for missing id."""
        ok = db.delete_publish_template("missing")
        assert ok is False


# ---------------------------------------------------------------------------
# PublishTemplateService tests
# ---------------------------------------------------------------------------


class TestPublishTemplateService:
    """Test the PublishTemplateService business logic."""

    def test_create_template(self, service, db):
        """create_template inserts and returns id + name."""
        result = service.create_template(
            user_id="user-1",
            name="My Template",
            platforms=["youtube", "bilibili"],
            title_template="{{video_name}} - Episode {{ep}}",
            description_template="Watch {{video_name}}",
            tags=["gaming", "fun"],
            platform_options={"youtube": {"category": "Gaming"}},
        )
        assert "id" in result
        assert result["name"] == "My Template"

        stored = db.get_publish_template(result["id"])
        assert stored is not None
        assert json.loads(stored["platforms"]) == ["youtube", "bilibili"]

    def test_list_templates(self, service):
        """list_templates returns all templates."""
        service.create_template(user_id="u1", name="T1", platforms=["youtube"])
        service.create_template(user_id="u2", name="T2", platforms=["bilibili"])
        templates = service.list_templates()
        assert len(templates) == 2

    def test_list_templates_by_user(self, service):
        """list_templates filters by user_id."""
        service.create_template(user_id="alice", name="A", platforms=[])
        service.create_template(user_id="bob", name="B", platforms=[])
        assert len(service.list_templates(user_id="alice")) == 1

    def test_get_template(self, service):
        """get_template returns the template."""
        result = service.create_template(user_id="", name="T", platforms=["tiktok"])
        template = service.get_template(result["id"])
        assert template is not None
        assert template["name"] == "T"

    def test_get_template_not_found(self, service):
        """get_template returns None for missing id."""
        assert service.get_template("nonexistent") is None

    def test_update_template(self, service):
        """update_template changes fields."""
        result = service.create_template(user_id="", name="Old", platforms=[])
        ok = service.update_template(result["id"], name="New")
        assert ok is True
        updated = service.get_template(result["id"])
        assert updated["name"] == "New"

    def test_delete_template(self, service):
        """delete_template removes the template."""
        result = service.create_template(user_id="", name="Del", platforms=[])
        ok = service.delete_template(result["id"])
        assert ok is True
        assert service.get_template(result["id"]) is None

    def test_apply_template_basic(self, service):
        """apply_template generates task specs for each platform."""
        result = service.create_template(
            user_id="",
            name="Multi-plat",
            platforms=["youtube", "bilibili", "tiktok"],
            title_template="My Video",
            description_template="Great video",
            tags=["tag1", "tag2"],
        )
        tasks = service.apply_template(result["id"], video_path="/tmp/video.mp4")
        assert len(tasks) == 3
        assert tasks[0]["platform"] == "youtube"
        assert tasks[1]["platform"] == "bilibili"
        assert tasks[2]["platform"] == "tiktok"
        for t in tasks:
            assert t["video_path"] == "/tmp/video.mp4"
            assert t["title"] == "My Video"
            assert t["description"] == "Great video"
            assert t["tags"] == ["tag1", "tag2"]

    def test_apply_template_variable_substitution(self, service):
        """apply_template substitutes variables in title and description."""
        result = service.create_template(
            user_id="",
            name="Vars",
            platforms=["youtube"],
            title_template="{{video_name}} - Episode {{ep}}",
            description_template="Watch {{video_name}} now!",
        )
        tasks = service.apply_template(
            result["id"],
            video_path="/tmp/v.mp4",
            title_vars={"video_name": "Awesome Show", "ep": "5"},
            desc_vars={"video_name": "Awesome Show"},
        )
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Awesome Show - Episode 5"
        assert tasks[0]["description"] == "Watch Awesome Show now!"

    def test_apply_template_platform_options(self, service):
        """apply_template includes per-platform options."""
        result = service.create_template(
            user_id="",
            name="Opts",
            platforms=["youtube", "bilibili"],
            platform_options={
                "youtube": {"category": "Gaming", "privacy": "public"},
                "bilibili": {"tid": 17},
            },
        )
        tasks = service.apply_template(result["id"], video_path="/tmp/v.mp4")
        assert tasks[0]["platform_options"] == {"category": "Gaming", "privacy": "public"}
        assert tasks[1]["platform_options"] == {"tid": 17}

    def test_apply_template_empty_platforms(self, service):
        """apply_template with empty platforms returns empty list."""
        result = service.create_template(
            user_id="",
            name="Empty",
            platforms=[],
        )
        tasks = service.apply_template(result["id"], video_path="/tmp/v.mp4")
        assert tasks == []

    def test_apply_template_nonexistent(self, service):
        """apply_template with nonexistent template returns empty list."""
        tasks = service.apply_template("nonexistent", video_path="/tmp/v.mp4")
        assert tasks == []

    def test_create_template_defaults(self, service, db):
        """create_template handles None defaults correctly."""
        result = service.create_template(
            user_id="",
            name="Defaults",
            platforms=["youtube"],
        )
        stored = db.get_publish_template(result["id"])
        assert json.loads(stored["tags"]) == []
        assert json.loads(stored["platform_options"]) == {}
        assert stored["title_template"] == ""
        assert stored["description_template"] == ""
