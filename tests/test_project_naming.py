import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core import project_naming  # noqa: E402


def test_build_project_name_prefers_translated_title_then_source_title():
    assert project_naming.build_project_name(
        translated_title="规范项目名",
        source_title="Original Title",
        source_url="https://example.com/video",
        task_id="vf_demo",
    ) == "规范项目名"
    assert project_naming.build_project_name(
        translated_title="",
        source_title="Original Title",
        source_url="https://example.com/video",
        task_id="vf_demo",
    ) == "Original Title"


def test_build_project_name_uses_local_file_stem_before_raw_path():
    assert project_naming.build_project_name(
        translated_title="",
        source_title="",
        source_url="/tmp/demo-video.mp4",
        task_id="vf_demo",
    ) == "demo-video"


@pytest.mark.asyncio
async def test_resolve_project_titles_skips_remote_resolution_when_disabled(monkeypatch):
    monkeypatch.setenv("VF_DISABLE_TITLE_RESOLVE", "1")

    async def _fail_fetch(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("fetch_remote_source_title should not be called")

    async def _fail_translate(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("translate_project_name should not be called")

    monkeypatch.setattr(project_naming, "fetch_remote_source_title", _fail_fetch)
    monkeypatch.setattr(project_naming, "translate_project_name", _fail_translate)

    resolved = await project_naming.resolve_project_titles(
        source_url="https://www.youtube.com/watch?v=disabled_case",
        source_title="Original Title",
        source_lang="en",
        target_lang="zh_cn",
    )

    assert resolved.source_title == "Original Title"
    assert resolved.project_name == ""


@pytest.mark.asyncio
async def test_resolve_project_titles_fetches_remote_title_and_translates(monkeypatch):
    monkeypatch.delenv("VF_DISABLE_TITLE_RESOLVE", raising=False)

    async def _fake_fetch(source_url, *, timeout_seconds=None, downloader=None):
        assert source_url.endswith("remote_case")
        return "Remote Original Title"

    async def _fake_translate(source_title, *, source_lang, target_lang, translator=None):
        assert source_title == "Remote Original Title"
        assert source_lang == "en"
        assert target_lang == "zh_cn"
        return "远程项目名"

    monkeypatch.setattr(project_naming, "fetch_remote_source_title", _fake_fetch)
    monkeypatch.setattr(project_naming, "translate_project_name", _fake_translate)

    resolved = await project_naming.resolve_project_titles(
        source_url="https://www.youtube.com/watch?v=remote_case",
        source_title="",
        source_lang="en",
        target_lang="zh_cn",
    )

    assert resolved.source_title == "Remote Original Title"
    assert resolved.project_name == "远程项目名"
