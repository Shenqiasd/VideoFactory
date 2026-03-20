import json
from urllib.parse import unquote

from api.routes import tasks as tasks_routes
from api.routes import distribute as distribute_routes
from api.routes import factory as factory_routes
from core import project_naming
from core.task import Task, TaskState
from distribute.scheduler import PublishJob
from pathlib import Path


def test_create_alias_accepts_form_payload(client):
    response = client.post(
        "/api/tasks/create",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=single_case",
            "source_lang": "en",
            "target_lang": "zh_cn",
            "create_clips": "on",
            "create_article": "on",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"].startswith("vf_")
    assert payload["state"] == TaskState.QUEUED.value

    task = tasks_routes.get_task_store().get(payload["task_id"])
    assert task is not None
    assert task.source_url == "https://www.youtube.com/watch?v=single_case"
    assert task.enable_short_clips is True
    assert task.enable_article is True


def test_create_alias_htmx_request_returns_hx_redirect(client):
    response = client.post(
        "/api/tasks/create",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=single_htmx_case",
            "source_lang": "en",
            "target_lang": "zh_cn",
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert response.headers.get("HX-Redirect") == "/tasks"


def test_batch_create_alias_htmx_request_returns_hx_redirect(client):
    response = client.post(
        "/api/tasks/batch-create",
        data={
            "urls": "https://www.youtube.com/watch?v=batch_htmx_case",
            "source_lang": "en",
            "target_lang": "zh_cn",
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert response.headers.get("HX-Redirect") == "/tasks"


def test_create_alias_browser_form_post_redirects_to_tasks(client):
    response = client.post(
        "/api/tasks/create",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=single_browser_case",
            "source_lang": "en",
            "target_lang": "zh_cn",
        },
        headers={"Accept": "text/html,application/xhtml+xml"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers.get("location") == "/tasks"


def test_batch_create_alias_creates_multiple_tasks(client):
    response = client.post(
        "/api/tasks/batch-create",
        data={
            "urls": "\n".join(
                [
                    "https://www.youtube.com/watch?v=case_a",
                    "https://www.youtube.com/watch?v=case_b",
                    "https://www.youtube.com/watch?v=case_a",
                ]
            )
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert len(payload["tasks"]) == 2
    assert all(task["task_id"].startswith("vf_") for task in payload["tasks"])


def test_batch_create_alias_rejects_empty_input(client):
    response = client.post("/api/tasks/batch-create", data={"urls": " \n \n"})

    assert response.status_code == 400
    assert response.json()["detail"] == "未提供有效URL"


def test_json_create_respects_explicit_options(client):
    response = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=explicit_case",
            "task_scope": "full",
            "enable_tts": False,
            "enable_short_clips": False,
            "enable_article": False,
            "embed_subtitle_type": "none",
        },
    )
    assert response.status_code == 200

    task_id = response.json()["task_id"]
    task = tasks_routes.get_task_store().get(task_id)
    assert task is not None
    assert task.task_scope == "full"
    assert task.enable_tts is False
    assert task.enable_short_clips is False
    assert task.enable_article is False
    assert task.embed_subtitle_type == "none"


def test_json_create_persists_creation_config(client):
    response = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=creation_case",
            "task_scope": "full",
            "creation_config": {
                "clip_count": 7,
                "duration_min": 45,
                "platforms": ["douyin", "bilibili"],
                "review_mode": "manual",
            },
        },
    )
    assert response.status_code == 200

    task = tasks_routes.get_task_store().get(response.json()["task_id"])
    assert task is not None
    assert task.creation_config["clip_count"] == 7
    assert task.creation_config["duration_min"] == 45
    assert task.creation_config["platforms"] == ["douyin", "bilibili"]
    assert task.creation_config["review_mode"] == "required"


def test_json_create_detail_returns_normalized_creation_config(client):
    response = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=creation_detail_case",
            "task_scope": "full",
            "creation_config": {
                "clip_count": 6,
                "duration_min": 25,
                "duration_max": 80,
                "crop_mode": "center",
                "review_mode": "manual",
                "platforms": ["douyin", "xiaohongshu"],
                "bgm_path": "/tmp/demo-bgm.mp3",
                "bgm_volume": 0.33,
            },
        },
    )
    assert response.status_code == 200

    task_id = response.json()["task_id"]
    detail = client.get(f"/api/tasks/{task_id}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["creation_config"]["clip_count"] == 6
    assert payload["creation_config"]["duration_min"] == 25
    assert payload["creation_config"]["duration_max"] == 80
    assert payload["creation_config"]["crop_mode"] == "center"
    assert payload["creation_config"]["review_mode"] == "required"
    assert payload["creation_config"]["platforms"] == ["douyin", "xiaohongshu"]
    assert payload["creation_config"]["bgm_path"] == "/tmp/demo-bgm.mp3"
    assert payload["creation_config"]["bgm_volume"] == 0.33


def test_json_create_resolves_project_name_from_source_title(client, monkeypatch):
    monkeypatch.delenv("VF_DISABLE_TITLE_RESOLVE", raising=False)

    async def _fake_translate(source_title, *, source_lang, target_lang, translator=None):
        assert source_title == "Original Video Title"
        assert source_lang == "en"
        assert target_lang == "zh_cn"
        return "规范项目名"

    monkeypatch.setattr(project_naming, "translate_project_name", _fake_translate)

    response = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=project_title_case",
            "source_title": "Original Video Title",
            "source_lang": "en",
            "target_lang": "zh_cn",
        },
    )
    assert response.status_code == 200

    task_id = response.json()["task_id"]
    task = tasks_routes.get_task_store().get(task_id)
    assert task is not None
    assert task.source_title == "Original Video Title"
    assert task.translated_title == "规范项目名"

    detail = client.get(f"/api/tasks/{task_id}")
    assert detail.status_code == 200
    assert detail.json()["project_name"] == "规范项目名"


def test_json_create_fetches_remote_title_when_source_title_missing(client, monkeypatch):
    monkeypatch.delenv("VF_DISABLE_TITLE_RESOLVE", raising=False)

    async def _fake_fetch(source_url, *, timeout_seconds=None, downloader=None):
        assert source_url.endswith("missing_title_case")
        return "Remote Original Title"

    async def _fake_translate(source_title, *, source_lang, target_lang, translator=None):
        assert source_title == "Remote Original Title"
        return "远程项目名"

    monkeypatch.setattr(project_naming, "fetch_remote_source_title", _fake_fetch)
    monkeypatch.setattr(project_naming, "translate_project_name", _fake_translate)

    response = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=missing_title_case",
            "source_lang": "en",
            "target_lang": "zh_cn",
        },
    )
    assert response.status_code == 200

    task_id = response.json()["task_id"]
    task = tasks_routes.get_task_store().get(task_id)
    assert task is not None
    assert task.source_title == "Remote Original Title"
    assert task.translated_title == "远程项目名"


def test_create_alias_persists_creation_config_from_form(client):
    response = client.post(
        "/api/tasks/create",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=form_creation_case",
            "source_lang": "en",
            "target_lang": "zh_cn",
            "task_scope": "full",
            "creation_clip_count": "4",
            "creation_duration_min": "20",
            "creation_duration_max": "75",
            "creation_crop_mode": "center",
            "creation_review_mode": "required",
            "creation_platforms": "douyin,bilibili",
            "creation_bgm_path": "/tmp/form-bgm.mp3",
            "creation_bgm_volume": "0.4",
            "creation_transition": "fade",
            "creation_transition_duration": "0.5",
        },
    )
    assert response.status_code == 200

    task_id = response.json()["task_id"]
    task = tasks_routes.get_task_store().get(task_id)
    assert task is not None
    assert task.creation_config["clip_count"] == 4
    assert task.creation_config["duration_min"] == 20
    assert task.creation_config["duration_max"] == 75
    assert task.creation_config["crop_mode"] == "center"
    assert task.creation_config["platforms"] == ["douyin", "bilibili"]
    assert task.creation_config["bgm_path"] == "/tmp/form-bgm.mp3"
    assert task.creation_config["bgm_volume"] == 0.4
    assert task.creation_config["transition_duration"] == 0.5


def test_create_alias_respects_form_toggles(client):
    response = client.post(
        "/api/tasks/create",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=toggle_case",
            "source_lang": "en",
            "target_lang": "zh_cn",
            "task_scope": "full",
            "create_clips": "false",
            "create_article": "false",
        },
    )
    assert response.status_code == 200

    task_id = response.json()["task_id"]
    task = tasks_routes.get_task_store().get(task_id)
    assert task is not None
    assert task.task_scope == "full"
    assert task.enable_short_clips is False
    assert task.enable_article is False


def test_cancel_task_transitions_to_failed(client):
    created = client.post(
        "/api/tasks/",
        json={"source_url": "https://www.youtube.com/watch?v=cancel_case"},
    )
    task_id = created.json()["task_id"]

    response = client.post(f"/api/tasks/{task_id}/cancel")
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == TaskState.FAILED.value
    assert payload["forced_transition"] is False

    detail = client.get(f"/api/tasks/{task_id}")
    assert detail.status_code == 200
    assert detail.json()["state"] == TaskState.FAILED.value


def test_cancel_task_forces_failed_when_transition_is_invalid(client):
    created = client.post(
        "/api/tasks/",
        json={"source_url": "https://www.youtube.com/watch?v=forced_cancel_case"},
    )
    task_id = created.json()["task_id"]

    store = tasks_routes.get_task_store()
    task = store.get(task_id)
    task.state = TaskState.READY_TO_PUBLISH.value
    store.update(task)

    response = client.post(f"/api/tasks/{task_id}/cancel")
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == TaskState.FAILED.value
    assert payload["forced_transition"] is True


def test_cancel_task_rejects_terminal_states(client):
    created = client.post(
        "/api/tasks/",
        json={"source_url": "https://www.youtube.com/watch?v=terminal_case"},
    )
    task_id = created.json()["task_id"]

    store = tasks_routes.get_task_store()
    task = store.get(task_id)
    task.state = TaskState.COMPLETED.value
    store.update(task)

    response = client.post(f"/api/tasks/{task_id}/cancel")
    assert response.status_code == 400
    assert "任务不可取消" in response.json()["detail"]


def test_delete_task_removes_task_from_store(client):
    created = client.post(
        "/api/tasks/",
        json={"source_url": "https://www.youtube.com/watch?v=delete_case"},
    )
    task_id = created.json()["task_id"]

    response = client.delete(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    assert "已删除" in response.json()["message"]

    detail = client.get(f"/api/tasks/{task_id}")
    assert detail.status_code == 404


def test_task_detail_contains_timeline(client):
    created = client.post(
        "/api/tasks/",
        json={"source_url": "https://www.youtube.com/watch?v=timeline_case"},
    )
    task_id = created.json()["task_id"]

    detail = client.get(f"/api/tasks/{task_id}")
    assert detail.status_code == 200
    payload = detail.json()
    assert isinstance(payload["timeline"], list)
    assert payload["timeline"]


def test_task_detail_contains_global_review_report(client):
    created = client.post(
        "/api/tasks/",
        json={"source_url": "https://www.youtube.com/watch?v=global_review_detail_case"},
    )
    task_id = created.json()["task_id"]

    task = tasks_routes.get_task_store().get(task_id)
    task.global_review_report = {
        "status": "passed",
        "passed": True,
        "domain": {"name": "music", "confidence": 0.97},
    }
    tasks_routes.get_task_store().update(task)

    detail = client.get(f"/api/tasks/{task_id}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["global_review_report"]["status"] == "passed"
    assert payload["global_review_report"]["domain"]["name"] == "music"


def test_task_detail_uses_translation_fields_instead_of_legacy_klic_fields(client):
    created = client.post(
        "/api/tasks/",
        json={"source_url": "https://www.youtube.com/watch?v=translation_detail_case"},
    )
    task_id = created.json()["task_id"]

    task = tasks_routes.get_task_store().get(task_id)
    task.translation_task_id = "selfhosted_youtube_test"
    task.translation_progress = 88
    tasks_routes.get_task_store().update(task)

    detail = client.get(f"/api/tasks/{task_id}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["translation_task_id"] == "selfhosted_youtube_test"
    assert payload["translation_progress"] == 88
    assert "project_name" in payload
    assert "klic_task_id" not in payload
    assert "klic_progress" not in payload


def test_task_from_dict_migrates_legacy_klic_fields():
    migrated = Task.from_dict(
        {
            "task_id": "vf_migrated_case",
            "source_url": "https://example.com/video",
            "klic_task_id": "legacy_klic_123",
            "klic_progress": 73,
        }
    )

    assert migrated.translation_task_id == "legacy_klic_123"
    assert migrated.translation_progress == 73


def test_production_status_uses_translation_fields(client):
    created = client.post(
        "/api/tasks/",
        json={"source_url": "https://www.youtube.com/watch?v=production_status_case"},
    )
    task_id = created.json()["task_id"]

    task = tasks_routes.get_task_store().get(task_id)
    task.translation_task_id = "selfhosted_whisper_test"
    task.translation_progress = 64
    tasks_routes.get_task_store().update(task)

    response = client.get(f"/api/production/status/{task_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["translation_task_id"] == "selfhosted_whisper_test"
    assert payload["translation_progress"] == 64
    assert "klic_task_id" not in payload
    assert "klic_progress" not in payload


def test_creation_summary_groups_segments_variants_and_covers(client):
    created = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=creation_summary_case",
            "task_scope": "full",
        },
    )
    assert created.status_code == 200
    task_id = created.json()["task_id"]

    task = tasks_routes.get_task_store().get(task_id)
    task.creation_state = {
        "stage": "completed",
        "status": "completed",
        "segments_total": 1,
        "variants_total": 1,
        "used_fallback": False,
        "selected_segments": [
            {
                "segment_id": "seg_001",
                "title": "知识点片段 1",
                "start": 12.0,
                "end": 55.0,
                "duration": 43.0,
                "crop_track": {"strategy": "yolo", "focus_class": "person"},
            }
        ],
    }
    task.creation_status = {
        "review_required": True,
        "review_status": "pending",
        "status": "completed",
    }
    task.products = [
        {
            "type": "short_clip",
            "platform": "douyin",
            "title": "知识点片段 1 · douyin",
            "local_path": "/tmp/seg001_douyin.mp4",
            "metadata": {"segment_id": "seg_001", "review_status": "pending"},
        },
        {
            "type": "cover",
            "platform": "all",
            "title": "横版封面",
            "local_path": "/tmp/cover_horizontal.jpg",
            "metadata": {"cover_type": "horizontal"},
        },
    ]
    tasks_routes.get_task_store().update(task)

    response = client.get(f"/api/tasks/{task_id}/creation-summary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["config"]["clip_count"] >= 1
    assert payload["status"]["review_status"] == "pending"
    assert payload["actions"]["can_approve"] is True
    assert len(payload["segments"]) == 1
    assert payload["segments"][0]["segment_id"] == "seg_001"
    assert len(payload["variants_by_segment"]) == 1
    assert payload["variants_by_segment"][0]["segment_id"] == "seg_001"
    assert payload["variants_by_segment"][0]["variants"][0]["platform"] == "douyin"
    assert len(payload["covers"]) == 1
    assert payload["covers"][0]["metadata"]["cover_type"] == "horizontal"


def test_factory_review_approve_endpoint_updates_creation_status(client):
    task = tasks_routes.get_task_store().create(
        source_url="https://www.youtube.com/watch?v=review_gate_case",
        state=TaskState.READY_TO_PUBLISH.value,
        creation_status={
            "review_required": True,
            "review_status": "pending",
            "status": "completed",
        },
    )

    response = client.post("/api/factory/review/approve", json={"task_id": task.task_id})
    assert response.status_code == 200
    assert response.json()["review_status"] == "approved"

    updated = factory_routes.get_task_store().get(task.task_id)
    assert updated.creation_status["review_status"] == "approved"


def test_task_artifacts_list_and_download(client, tmp_path):
    source_file = tmp_path / "source_video.mp4"
    subtitle_file = tmp_path / "bilingual_srt.srt"
    source_file.write_bytes(b"fake-video")
    subtitle_file.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")

    created = client.post(
        "/api/tasks/",
        json={
            "source_url": "https://www.youtube.com/watch?v=artifact_case",
            "source_title": "artifact title",
        },
    )
    task_id = created.json()["task_id"]

    task = tasks_routes.get_task_store().get(task_id)
    task.source_local_path = str(source_file)
    task.subtitle_path = str(subtitle_file)
    tasks_routes.get_task_store().update(task)

    list_resp = client.get(f"/api/tasks/{task_id}/artifacts")
    assert list_resp.status_code == 200
    payload = list_resp.json()
    assert payload["count"] >= 2

    subtitle_artifact = next(a for a in payload["artifacts"] if a["local_path"] == str(subtitle_file))
    download_resp = client.get(subtitle_artifact["download_url"])
    assert download_resp.status_code == 200
    assert download_resp.content == subtitle_file.read_bytes()
    content_disposition = unquote(download_resp.headers.get("content-disposition", ""))
    assert subtitle_artifact["download_filename"] == "artifact_title_双语字幕.srt"
    assert "artifact_title_双语字幕.srt" in content_disposition


def test_task_artifacts_download_falls_back_to_r2(client, monkeypatch, tmp_path):
    created = client.post(
        "/api/tasks/",
        json={"source_url": "https://www.youtube.com/watch?v=r2_case"},
    )
    task_id = created.json()["task_id"]

    # 仅保留 R2 路径，模拟本地文件已清理
    task = tasks_routes.get_task_store().get(task_id)
    task.products = [
        {
            "type": "long_video",
            "platform": "all",
            "local_path": str(tmp_path / "missing_video.mp4"),
            "r2_path": f"processed/{task_id}/long_video/long_video.mp4",
            "title": "long_video.mp4",
        }
    ]
    tasks_routes.get_task_store().update(task)

    def _fake_download(self, r2_path, local_path):
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        Path(local_path).write_bytes(b"from-r2")
        return True

    monkeypatch.setattr("api.routes.tasks.StorageManager.download_from_r2", _fake_download)

    list_resp = client.get(f"/api/tasks/{task_id}/artifacts")
    assert list_resp.status_code == 200
    artifact = next(a for a in list_resp.json()["artifacts"] if a["r2_path"])
    assert artifact["downloadable"] is True
    assert artifact["download_filename"] == "task_{}_长视频.mp4".format(task_id[:8])

    download_resp = client.get(artifact["download_url"])
    assert download_resp.status_code == 200
    assert download_resp.content == b"from-r2"


def test_task_artifact_download_supports_inline_image_preview(client, tmp_path):
    cover_file = tmp_path / "cover.png"
    cover_file.write_bytes(b"fake-cover")

    created = client.post(
        "/api/tasks/",
        json={"source_url": "https://www.youtube.com/watch?v=inline_cover_case"},
    )
    task_id = created.json()["task_id"]

    task = tasks_routes.get_task_store().get(task_id)
    task.products = [
        {
            "type": "cover",
            "platform": "all",
            "local_path": str(cover_file),
            "metadata": {"cover_type": "horizontal"},
        }
    ]
    tasks_routes.get_task_store().update(task)

    list_resp = client.get(f"/api/tasks/{task_id}/artifacts")
    assert list_resp.status_code == 200
    artifact = next(a for a in list_resp.json()["artifacts"] if a["type"] == "cover")

    download_resp = client.get(artifact["download_url"], params={"inline": 1})
    assert download_resp.status_code == 200
    assert download_resp.content == b"fake-cover"
    assert download_resp.headers["content-type"].startswith("image/png")
    content_disposition = unquote(download_resp.headers.get("content-disposition", ""))
    assert content_disposition.startswith("inline;")


def test_create_alias_accepts_subtitle_style_fields(client):
    response = client.post(
        "/api/tasks/create",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=style_case",
            "source_lang": "en",
            "target_lang": "zh_cn",
            "subtitle_cn_font_size": "30",
            "subtitle_en_font_size": "18",
            "subtitle_cn_margin_v": "80",
            "subtitle_en_margin_v": "40",
            "subtitle_cn_alignment": "2",
            "subtitle_en_alignment": "8",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["task_id"]
    detail = client.get(f"/api/tasks/{task_id}")
    assert detail.status_code == 200
    style = detail.json()["subtitle_style"]
    assert style["cn_font_size"] == 30
    assert style["en_font_size"] == 18
    assert style["cn_margin_v"] == 80
    assert style["en_margin_v"] == 40
    assert style["cn_alignment"] == 2
    assert style["en_alignment"] == 8


def test_subtitle_style_defaults_roundtrip(client):
    get_resp = client.get("/api/system/subtitle-style-defaults")
    assert get_resp.status_code == 200
    assert "subtitle_style" in get_resp.json()

    post_resp = client.post(
        "/api/system/subtitle-style-defaults",
        json={
            "subtitle_style": {
                "cn_font_size": 32,
                "en_font_size": 22,
                "cn_margin_v": 90,
                "en_margin_v": 48,
                "cn_alignment": 2,
                "en_alignment": 2,
            }
        },
    )
    assert post_resp.status_code == 200
    assert post_resp.json()["subtitle_style"]["cn_font_size"] == 32

    get_resp2 = client.get("/api/system/subtitle-style-defaults")
    assert get_resp2.status_code == 200
    assert get_resp2.json()["subtitle_style"]["cn_font_size"] == 32


def test_subtitle_preview_endpoint_returns_video_url(client, monkeypatch, tmp_path):
    captured = {}

    async def _fake_download(source_url, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"source")
        return True, ""

    async def _fake_burn(self, video_path, subtitle_path, output_path, subtitle_style=None, **kwargs):
        captured["kwargs"] = kwargs
        captured["subtitle_path"] = str(subtitle_path)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"preview-video")
        return True, {
            "font_requested": "Hiragino Sans GB",
            "font_used": "Hiragino Sans GB",
            "visibility_score": 0.0123,
            "attempts": [{"font": "Hiragino Sans GB", "ffmpeg_ok": True, "visibility_score": 0.0123}],
        }

    monkeypatch.setattr("api.routes.tasks._download_preview_source", _fake_download)
    monkeypatch.setattr("api.routes.tasks.LongVideoProcessor.burn_subtitles_with_debug", _fake_burn)

    response = client.post(
        "/api/tasks/subtitle-style/preview",
        json={
            "source_url": "https://www.youtube.com/watch?v=preview_case",
            "source_lang": "en",
            "target_lang": "zh_cn",
            "task_scope": "subtitle_only",
            "subtitle_style": {
                "cn_font_size": 28,
                "en_font_size": 18,
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["preview_id"].startswith("pv_")
    assert payload["preview_url"].startswith("/api/tasks/subtitle-style/preview/")
    assert captured["kwargs"]["allow_soft_fallback"] is False
    assert captured["kwargs"]["probe_font_candidates"] is True
    assert captured["kwargs"]["visibility_check"] is True
    assert payload["render_debug"]["font_used"] == "Hiragino Sans GB"

    sample_srt = Path(captured["subtitle_path"]).read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:04,000" in sample_srt

    video_resp = client.get(payload["preview_url"])
    assert video_resp.status_code == 200
    assert video_resp.content == b"preview-video"


def test_subtitle_preview_endpoint_returns_visibility_error(client, monkeypatch):
    async def _fake_download(source_url, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"source")
        return True, ""

    async def _fake_burn(self, video_path, subtitle_path, output_path, subtitle_style=None, **kwargs):
        return False, {"error": "字幕渲染失败：字体不可用或字幕位置越界（可见性校验未通过）"}

    monkeypatch.setattr("api.routes.tasks._download_preview_source", _fake_download)
    monkeypatch.setattr("api.routes.tasks.LongVideoProcessor.burn_subtitles_with_debug", _fake_burn)

    response = client.post(
        "/api/tasks/subtitle-style/preview",
        json={
            "source_url": "https://www.youtube.com/watch?v=preview_case",
            "source_lang": "en",
            "target_lang": "zh_cn",
        },
    )
    assert response.status_code == 500


def test_asr_tts_settings_roundtrip(client, monkeypatch, tmp_path):
    from core.config import Config
    import yaml

    src_config_path = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
    if not src_config_path.exists():
        src_config_path = Path(__file__).resolve().parents[2] / "config" / "settings.example.yaml"
    test_config_path = tmp_path / "settings.yaml"
    test_config_path.write_text(src_config_path.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setenv("VF_CONFIG", str(test_config_path))
    Config.reset()

    get_resp = client.get("/api/system/settings/asr-tts")
    assert get_resp.status_code == 200
    assert "asr" in get_resp.json()
    assert "tts" in get_resp.json()

    post_resp = client.post(
        "/api/system/settings/asr-tts",
        json={
            "asr": {
                "provider": "youtube",
                "allow_fallback": True,
                "allow_router_with_tts": False,
                "fallback_order": ["youtube", "whisper"],
                "youtube_skip_download": True,
                "youtube_preferred_langs": ["en", "en-US"],
                "whisper": {
                    "base_url": "http://127.0.0.1:8866/v1",
                    "model": "whisper-1",
                    "timeout": 650,
                },
                "volcengine": {
                    "enabled": False,
                    "app_id": "",
                    "token": "",
                    "http_url": "",
                    "ws_url": "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel",
                    "timeout": 120,
                },
            },
            "tts": {
                "provider": "volcengine",
                "fallback_order": ["volcengine"],
                "volcengine": {
                    "enabled": True,
                    "appid": "app_x",
                    "access_token": "token_x",
                    "cluster": "volcano_tts",
                    "api_url": "https://example.com/tts",
                    "default_voice": "BV001_streaming",
                    "available_voices": [
                        {"id": "BV001_streaming", "name": "通用女声", "language": "zh-CN"},
                        {"id": "BV002_streaming", "name": "通用男声", "language": "zh-CN"},
                    ],
                    "app_id": "app_x",
                    "token": "token_x",
                    "clone_url": "",
                    "synthesis_url": "https://example.com/tts",
                    "voice_id": "BV001_streaming",
                    "timeout": 180,
                },
            },
            "translation": {
                "provider": "volcengine_ark",
                "strict_json": True,
                "local_llm": {
                    "enabled": True,
                    "base_url": "http://127.0.0.1:1234/v1",
                    "api_key": "",
                    "model": "qwen2.5-7b-instruct",
                    "timeout": 120,
                },
                "volcengine_ark": {
                    "enabled": True,
                    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                    "api_key": "ark_test_key",
                    "model": "doubao-seed-translation-250915",
                    "timeout": 60,
                },
                "llm": {
                    "timeout": 60,
                },
            },
        },
    )
    assert post_resp.status_code == 200
    payload = post_resp.json()
    assert payload["success"] is True
    assert payload["asr"]["provider"] == "youtube"
    assert "allow_klicstudio_fallback" not in payload["asr"]
    assert payload["tts"]["provider"] == "volcengine"
    assert payload["tts"]["fallback_order"] == ["volcengine"]
    assert payload["translation"]["provider"] == "volcengine_ark"
    assert payload["translation"]["local_llm"]["model"] == "qwen2.5-7b-instruct"

    get_resp2 = client.get("/api/system/settings/asr-tts")
    assert get_resp2.status_code == 200
    assert get_resp2.json()["asr"]["provider"] == "youtube"
    assert "allow_klicstudio_fallback" not in get_resp2.json()["asr"]
    assert get_resp2.json()["tts"]["provider"] == "volcengine"
    assert get_resp2.json()["tts"]["fallback_order"] == ["volcengine"]
    assert get_resp2.json()["translation"]["provider"] == "volcengine_ark"
    assert get_resp2.json()["translation"]["local_llm"]["base_url"] == "http://127.0.0.1:1234/v1"

    persisted = yaml.safe_load(test_config_path.read_text(encoding="utf-8"))
    assert persisted["asr"]["provider"] == "youtube"
    assert persisted["tts"]["provider"] == "volcengine"
    assert persisted["tts"]["volcengine"]["default_voice"] == "BV001_streaming"
    assert persisted["translation"]["provider"] == "volcengine_ark"
    assert persisted["translation"]["local_llm"]["timeout"] == 120


def test_asr_tts_settings_rejects_invalid_provider(client):
    response = client.post(
        "/api/system/settings/asr-tts",
        json={
            "asr": {
                "provider": "invalid_provider",
                "allow_fallback": True,
                "allow_router_with_tts": False,
                "fallback_order": ["youtube"],
                "youtube_skip_download": False,
                "youtube_preferred_langs": ["en"],
                "whisper": {
                    "base_url": "http://127.0.0.1:8866/v1",
                    "model": "whisper-1",
                    "timeout": 600,
                },
                "volcengine": {
                    "enabled": False,
                    "app_id": "",
                    "token": "",
                    "http_url": "",
                    "ws_url": "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel",
                    "timeout": 120,
                },
            },
            "tts": {
                "provider": "invalid_provider",
                "fallback_order": ["volcengine"],
                "volcengine": {
                    "enabled": False,
                    "app_id": "",
                    "token": "",
                    "clone_url": "",
                    "synthesis_url": "",
                    "voice_id": "",
                    "timeout": 120,
                },
            },
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert any("asr.provider 非法" in str(item.get("msg", "")) for item in detail)


def test_asr_tts_settings_get_normalizes_legacy_klic_values(client, monkeypatch, tmp_path):
    from core.config import Config
    import yaml

    src_config_path = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
    if not src_config_path.exists():
        src_config_path = Path(__file__).resolve().parents[2] / "config" / "settings.example.yaml"
    test_config_path = tmp_path / "settings.yaml"
    config_data = yaml.safe_load(src_config_path.read_text(encoding="utf-8"))
    config_data.setdefault("asr", {})
    config_data["asr"]["provider"] = "klicstudio"
    config_data["asr"]["allow_klicstudio_fallback"] = True
    config_data["asr"]["fallback_order"] = ["youtube", "klicstudio", "whisper"]
    config_data.setdefault("tts", {})
    config_data["tts"]["provider"] = "klicstudio"
    config_data["tts"]["fallback_order"] = ["volcengine", "klicstudio"]
    test_config_path.write_text(yaml.safe_dump(config_data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    monkeypatch.setenv("VF_CONFIG", str(test_config_path))
    Config.reset()

    response = client.get("/api/system/settings/asr-tts")

    assert response.status_code == 200
    payload = response.json()
    assert payload["asr"]["provider"] == "auto"
    assert "allow_klicstudio_fallback" not in payload["asr"]
    assert payload["asr"]["fallback_order"] == ["youtube", "whisper"]
    assert payload["tts"]["provider"] == "volcengine"
    assert payload["tts"]["fallback_order"] == ["volcengine"]


def test_system_tts_voices_endpoint(client):
    response = client.get("/api/system/tts/voices")
    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "volcengine"
    assert isinstance(payload["voices"], list)
    assert len(payload["voices"]) > 0


def test_system_translation_test_endpoint(client, monkeypatch):
    async def _fake_translate(self, *, text, source_lang="en", target_lang="zh-CN"):
        return f"{text}-zh"

    monkeypatch.setattr("api.routes.system.VolcengineArkTranslator.is_configured", lambda self: True)
    monkeypatch.setattr("api.routes.system.VolcengineArkTranslator.translate_text", _fake_translate)

    response = client.post(
        "/api/system/test/translation",
        json={
            "provider": "volcengine_ark",
            "text": "Hello world",
            "source_lang": "en",
            "target_lang": "zh-CN",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["provider"] == "volcengine_ark"
    assert payload["result"] == "Hello world-zh"


def test_system_translation_test_endpoint_accepts_runtime_overrides(client, monkeypatch):
    async def _fake_translate(self, *, text, source_lang="en", target_lang="zh-CN"):
        return f"{text}-override"

    monkeypatch.setattr("api.routes.system.VolcengineArkTranslator.translate_text", _fake_translate)

    response = client.post(
        "/api/system/test/translation",
        json={
            "provider": "volcengine_ark",
            "text": "Hello runtime",
            "source_lang": "en",
            "target_lang": "zh-CN",
            "enabled": True,
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "api_key": "ark_runtime_key",
            "model": "doubao-seed-translation-250915",
            "timeout": 30,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["provider"] == "volcengine_ark"
    assert payload["result"] == "Hello runtime-override"


def test_system_translation_test_endpoint_supports_local_llm_without_api_key(client, monkeypatch):
    async def _fake_translate(self, *, text, source_lang="en", target_lang="zh-CN"):
        return f"{text}-local"

    monkeypatch.setattr("api.routes.system.LocalLLMTranslator.translate_text", _fake_translate)

    response = client.post(
        "/api/system/test/translation",
        json={
            "provider": "local_llm",
            "text": "Hello local",
            "source_lang": "en",
            "target_lang": "zh-CN",
            "enabled": True,
            "base_url": "http://127.0.0.1:1234/v1",
            "model": "qwen2.5-7b-instruct",
            "timeout": 45,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["provider"] == "local_llm"
    assert payload["base_url"] == "http://127.0.0.1:1234/v1"
    assert payload["model"] == "qwen2.5-7b-instruct"
    assert payload["result"] == "Hello local-local"


def test_system_translation_test_endpoint_rejects_incomplete_local_llm_config(client):
    response = client.post(
        "/api/system/test/translation",
        json={
            "provider": "local_llm",
            "text": "Hello local",
            "source_lang": "en",
            "target_lang": "zh-CN",
            "enabled": True,
            "base_url": "http://127.0.0.1:1234/v1",
            "model": "",
        },
    )
    assert response.status_code == 400
    assert "本地翻译模型未配置完整或未启用" in response.json()["detail"]


def test_system_tts_test_endpoint(client, monkeypatch, tmp_path):
    from core.config import Config
    from tts.base import TTSResult
    import yaml

    src_config_path = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
    if not src_config_path.exists():
        src_config_path = Path(__file__).resolve().parents[2] / "config" / "settings.example.yaml"
    test_config_path = tmp_path / "settings.yaml"
    config_data = yaml.safe_load(src_config_path.read_text(encoding="utf-8"))
    config_data.setdefault("tts", {}).setdefault("volcengine", {})
    config_data["tts"]["provider"] = "volcengine"
    config_data["tts"]["volcengine"]["enabled"] = True
    config_data["tts"]["volcengine"]["appid"] = "test-appid"
    config_data["tts"]["volcengine"]["access_token"] = "test-token"
    config_data["tts"]["volcengine"]["api_url"] = "https://example.com/tts"
    test_config_path.write_text(yaml.safe_dump(config_data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    monkeypatch.setenv("VF_CONFIG", str(test_config_path))
    Config.reset()

    async def _fake_synthesize(self, *, text, output_path, source_audio_path=None, language="", voice_type=None):
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake-mp3-content")
        return TTSResult(audio_path=str(output), provider="volcengine", metadata={"voice_type": voice_type or ""})

    monkeypatch.setattr("api.routes.system.VolcengineTTS.synthesize", _fake_synthesize)

    response = client.post(
        "/api/system/test/tts",
        json={
            "provider": "volcengine",
            "text": "你好，测试语音",
            "voice_type": "BV001_streaming",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["voice_type"] == "BV001_streaming"
    assert payload["audio_url"].startswith("/api/system/test/tts/audio/")

    audio_resp = client.get(payload["audio_url"])
    assert audio_resp.status_code == 200
    assert audio_resp.content == b"fake-mp3-content"


def test_system_tts_test_endpoint_accepts_runtime_overrides(client, monkeypatch):
    from tts.base import TTSResult

    async def _fake_synthesize(self, *, text, output_path, source_audio_path=None, language="", voice_type=None):
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"runtime-mp3")
        return TTSResult(audio_path=str(output), provider="volcengine", metadata={"voice_type": voice_type or ""})

    monkeypatch.setattr("api.routes.system.VolcengineTTS.synthesize", _fake_synthesize)

    response = client.post(
        "/api/system/test/tts",
        json={
            "provider": "volcengine",
            "text": "运行时覆盖测试",
            "voice_type": "BV002_streaming",
            "enabled": True,
            "appid": "runtime-appid",
            "access_token": "runtime-token",
            "cluster": "volcano_tts",
            "api_url": "https://openspeech.bytedance.com/api/v1/tts",
            "timeout": 30,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["voice_type"] == "BV002_streaming"
    assert payload["audio_url"].startswith("/api/system/test/tts/audio/")

def test_task_artifacts_list_supports_r2_only_product(client):
    created = client.post(
        "/api/tasks/",
        json={"source_url": "https://www.youtube.com/watch?v=r2_only_case"},
    )
    task_id = created.json()["task_id"]

    task = tasks_routes.get_task_store().get(task_id)
    task.products = [
        {
            "type": "long_video",
            "platform": "all",
            "local_path": "",
            "r2_path": f"processed/{task_id}/long_video/long_video.mp4",
            "title": "long_video.mp4",
        }
    ]
    tasks_routes.get_task_store().update(task)

    list_resp = client.get(f"/api/tasks/{task_id}/artifacts")
    assert list_resp.status_code == 200
    payload = list_resp.json()
    artifact = payload["artifacts"][0]
    assert artifact["r2_path"].endswith("long_video.mp4")
    assert artifact["downloadable"] is True


def test_task_artifacts_list_ignores_malformed_products(client):
    created = client.post(
        "/api/tasks/",
        json={"source_url": "https://www.youtube.com/watch?v=bad_products_case"},
    )
    task_id = created.json()["task_id"]

    task = tasks_routes.get_task_store().get(task_id)
    task.products = [None, "broken", {"type": "long_video", "local_path": ""}]
    tasks_routes.get_task_store().update(task)

    list_resp = client.get(f"/api/tasks/{task_id}/artifacts")
    assert list_resp.status_code == 200
    payload = list_resp.json()
    assert isinstance(payload["artifacts"], list)


def test_task_artifacts_alias_route_works(client, tmp_path):
    source_file = tmp_path / "alias_source.mp4"
    source_file.write_bytes(b"alias")

    created = client.post(
        "/api/tasks/",
        json={"source_url": "https://www.youtube.com/watch?v=alias_case"},
    )
    task_id = created.json()["task_id"]

    task = tasks_routes.get_task_store().get(task_id)
    task.source_local_path = str(source_file)
    tasks_routes.get_task_store().update(task)

    list_resp = client.get(f"/tasks/{task_id}/artifacts")
    assert list_resp.status_code == 200
    payload = list_resp.json()
    assert payload["count"] >= 1

    artifact = payload["artifacts"][0]
    download_resp = client.get(f"/tasks/{task_id}/artifacts/{artifact['artifact_id']}/download")
    assert download_resp.status_code == 200


def test_publish_account_validation_and_default_binding(client, tmp_path):
    cookie_a = tmp_path / "douyin_a.json"
    cookie_a.write_text(
        json.dumps({"cookies": [{"domain": ".douyin.com", "name": "sessionid", "value": "a"}]}),
        encoding="utf-8",
    )
    cookie_b = tmp_path / "douyin_b.json"
    cookie_b.write_text(
        json.dumps({"cookies": [{"domain": ".douyin.com", "name": "sessionid_ss", "value": "b"}]}),
        encoding="utf-8",
    )

    first = client.post(
        "/api/publish/accounts",
        json={"platform": "douyin", "name": "A", "cookie_path": str(cookie_a)},
    )
    second = client.post(
        "/api/publish/accounts",
        json={"platform": "douyin", "name": "B", "cookie_path": str(cookie_b), "is_default": True},
    )
    assert first.status_code == 200
    assert second.status_code == 200

    second_id = second.json()["account_id"]
    test_resp = client.post(f"/api/publish/accounts/{second_id}/test")
    assert test_resp.status_code == 200
    payload = test_resp.json()
    assert payload["success"] is True
    assert payload["capabilities"]["cookie_exists"] is True

    accounts_resp = client.get("/api/publish/accounts?platform=douyin")
    accounts = accounts_resp.json()["accounts"]
    defaults = [a for a in accounts if a["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == second_id


def test_account_config_and_upload_endpoint(client, tmp_path):
    config_resp = client.get("/api/publish/accounts/config")
    assert config_resp.status_code == 200
    assert "storage_dir" in config_resp.json()

    cookie_file = tmp_path / "bilibili_cookie.json"
    cookie_file.write_text("{}", encoding="utf-8")
    with cookie_file.open("rb") as fh:
        response = client.post(
            "/api/publish/accounts/upload",
            data={"platform": "bilibili", "name": "上传账号"},
            files={"cookie_file": ("bilibili_cookie.json", fh, "application/json")},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["account_id"]
    assert payload["cookie_path"].endswith(".json")

    accounts = client.get("/api/publish/accounts?platform=bilibili").json()["accounts"]
    uploaded = next(account for account in accounts if account["name"] == "上传账号")
    assert uploaded["cookie_filename"].endswith(".json")


def test_account_format_validation_rejects_invalid_cookie_file(client, tmp_path):
    bad_cookie = tmp_path / "bad_cookie.txt"
    bad_cookie.write_text("not a valid cookie file", encoding="utf-8")

    response = client.post(
        "/api/publish/accounts",
        json={"platform": "douyin", "name": "坏Cookie", "cookie_path": str(bad_cookie)},
    )
    assert response.status_code == 200
    account_id = response.json()["account_id"]

    accounts = client.get("/api/publish/accounts?platform=douyin").json()["accounts"]
    target = next(account for account in accounts if account["id"] == account_id)
    assert target["status"] == "invalid"
    assert target["capabilities"]["format_valid"] is False
    assert "格式" in target["last_error"]


def test_account_upload_can_set_default_and_replace_cookie(client, tmp_path):
    first_cookie = tmp_path / "first.json"
    second_cookie = tmp_path / "second.txt"
    first_cookie.write_text("{}", encoding="utf-8")
    second_cookie.write_text("# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\ttoken\tabc\n", encoding="utf-8")

    with first_cookie.open("rb") as fh:
        response = client.post(
            "/api/publish/accounts/upload",
            data={"platform": "youtube", "name": "上传默认号", "is_default": "true"},
            files={"cookie_file": ("first.json", fh, "application/json")},
        )
    assert response.status_code == 200
    account_id = response.json()["account_id"]

    accounts = client.get("/api/publish/accounts?platform=youtube").json()["accounts"]
    target = next(account for account in accounts if account["id"] == account_id)
    assert target["is_default"] is True

    with second_cookie.open("rb") as fh:
        replace_resp = client.post(
            f"/api/publish/accounts/{account_id}/cookie",
            files={"cookie_file": ("second.txt", fh, "text/plain")},
        )
    assert replace_resp.status_code == 200
    account = replace_resp.json()["account"]
    assert account["cookie_filename"].endswith(".txt")
    assert account["capabilities"]["format_kind"] == "netscape"


def test_platform_specific_cookie_validation_accepts_matching_cookie(client, tmp_path):
    bilibili_cookie = tmp_path / "bilibili.json"
    bilibili_cookie.write_text(
        json.dumps(
            {
                "cookies": [
                    {"domain": ".bilibili.com", "name": "SESSDATA", "value": "abc"},
                    {"domain": ".bilibili.com", "name": "bili_jct", "value": "def"},
                ]
            }
        ),
        encoding="utf-8",
    )

    response = client.post(
        "/api/publish/accounts",
        json={"platform": "bilibili", "name": "B站匹配账号", "cookie_path": str(bilibili_cookie)},
    )
    assert response.status_code == 200
    account_id = response.json()["account_id"]
    accounts = client.get("/api/publish/accounts?platform=bilibili").json()["accounts"]
    target = next(account for account in accounts if account["id"] == account_id)
    assert target["status"] == "active"
    assert target["capabilities"]["platform_cookie_match"] is True


def test_platform_specific_cookie_validation_rejects_mismatched_cookie(client, tmp_path):
    youtube_cookie = tmp_path / "youtube.txt"
    youtube_cookie.write_text(
        "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc\n",
        encoding="utf-8",
    )

    response = client.post(
        "/api/publish/accounts",
        json={"platform": "douyin", "name": "抖音错配账号", "cookie_path": str(youtube_cookie)},
    )
    assert response.status_code == 200
    account_id = response.json()["account_id"]
    accounts = client.get("/api/publish/accounts?platform=douyin").json()["accounts"]
    target = next(account for account in accounts if account["id"] == account_id)
    assert target["status"] == "invalid"
    assert target["capabilities"]["platform_cookie_match"] is False
    assert "平台不匹配" in target["last_error"]


def test_publish_replay_accepts_partial_success_task(client, monkeypatch):
    async def _noop_run_due_jobs(scheduler):
        return None

    monkeypatch.setattr("api.routes.distribute._run_due_jobs", _noop_run_due_jobs)

    task = Task(
        task_id="vf_partial_contract",
        source_url="https://example.com",
        source_title="demo",
    )
    task.state = TaskState.PARTIAL_SUCCESS.value
    task.products = [{"type": "long_video", "platform": "all", "local_path": "/tmp/video.mp4", "title": "title"}]
    distribute_routes.get_task_store().update(task)

    scheduler = distribute_routes.get_scheduler()
    scheduler._queue = []
    failed_job = PublishJob(
        task_id=task.task_id,
        platform="bilibili",
        scheduled_time=0,
        product=task.products[0],
    )
    failed_job.status = "failed"
    failed_job.result = {"error": "mock failure"}
    scheduler._queue.append(failed_job)
    scheduler._save_queue()

    response = client.post("/api/distribute/replay", json={"task_id": task.task_id, "job_id": failed_job.job_id})
    assert response.status_code == 200
    assert response.json()["job_id"] == failed_job.job_id
    refreshed = distribute_routes.get_task_store().get(task.task_id)
    assert refreshed.state == TaskState.PUBLISHING.value


def test_manual_complete_and_fail_contract(client):
    task = Task(task_id="vf_manual_contract", source_url="https://example.com", source_title="demo")
    task.state = TaskState.PUBLISHING.value
    task.products = [{"type": "long_video", "platform": "all", "local_path": "/tmp/video.mp4", "title": "title"}]
    distribute_routes.get_task_store().update(task)
    scheduler = distribute_routes.get_scheduler()
    scheduler._queue = []

    job = PublishJob(task_id=task.task_id, platform="bilibili", scheduled_time=0, product=task.products[0])
    job.status = "manual_pending"
    job.result = {"manual_checklist": {"video_path": "/tmp/video.mp4"}}
    scheduler._queue.append(job)
    scheduler._save_queue()

    ok = client.post(
        "/api/distribute/manual/complete",
        json={"task_id": task.task_id, "job_id": job.job_id, "publish_url": "https://example.com/video"},
    )
    assert ok.status_code == 200
    assert ok.json()["job"]["status"] == "done"

    task2 = Task(task_id="vf_manual_fail", source_url="https://example.com", source_title="demo")
    task2.state = TaskState.PUBLISHING.value
    task2.products = task.products
    distribute_routes.get_task_store().update(task2)
    job2 = PublishJob(task_id=task2.task_id, platform="youtube", scheduled_time=0, product=task2.products[0])
    job2.status = "manual_pending"
    job2.result = {"manual_checklist": {"video_path": "/tmp/video.mp4"}}
    scheduler._queue = [job2]
    scheduler._save_queue()

    failed = client.post(
        "/api/distribute/manual/fail",
        json={"task_id": task2.task_id, "job_id": job2.job_id, "error": "cookie invalid"},
    )
    assert failed.status_code == 200
    assert failed.json()["job"]["status"] == "failed"


def test_publish_queue_partial_renders_retry_cancel_and_manual_controls(client):
    scheduler = distribute_routes.get_scheduler()
    task_id = "vf_publish_partial"
    product = {"type": "long_video", "local_path": "/tmp/video.mp4", "title": "title"}

    pending = PublishJob(task_id=task_id, platform="bilibili", scheduled_time=0, product=product)
    failed = PublishJob(task_id=task_id, platform="youtube", scheduled_time=0, product=product)
    manual = PublishJob(task_id=task_id, platform="douyin", scheduled_time=0, product=product)
    failed.status = "failed"
    failed.result = {"error": "mock failure"}
    manual.status = "manual_pending"
    manual.result = {"manual_checklist": {"video_path": "/tmp/video.mp4"}}
    scheduler._queue = [pending, failed, manual]
    scheduler._save_queue()

    response = client.get("/web/partials/publish_queue?platform=all")
    assert response.status_code == 200
    html = response.text
    assert "重试" in html
    assert "标记已发布" in html
    assert "取消" in html


def test_publish_request_persists_selected_account_binding(client, tmp_path, monkeypatch):
    async def _noop_run_due_jobs(scheduler):
        return None

    monkeypatch.setattr("api.routes.distribute._run_due_jobs", _noop_run_due_jobs)

    cookie_path = tmp_path / "bilibili_cookie.json"
    cookie_path.write_text("{}", encoding="utf-8")
    account_resp = client.post(
        "/api/publish/accounts",
        json={"platform": "bilibili", "name": "B站指定账号", "cookie_path": str(cookie_path)},
    )
    account_id = account_resp.json()["account_id"]

    task = Task(task_id="vf_bind_contract", source_url="https://example.com", source_title="demo")
    task.state = TaskState.READY_TO_PUBLISH.value
    task.products = [{"type": "long_video", "platform": "all", "local_path": "/tmp/video.mp4", "title": "title"}]
    distribute_routes.get_task_store().update(task)

    response = client.post(
        "/api/distribute/publish",
        json={
            "task_id": task.task_id,
            "platforms": ["bilibili"],
            "publish_accounts": {"bilibili": account_id},
        },
    )
    assert response.status_code == 200
    assert response.json()["publish_accounts"] == {"bilibili": account_id}

    refreshed = distribute_routes.get_task_store().get(task.task_id)
    assert refreshed.publish_accounts == {"bilibili": account_id}

    scheduler = distribute_routes.get_scheduler()
    jobs = [job for job in scheduler._queue if job.task_id == task.task_id]
    assert len(jobs) == 1
    assert jobs[0].metadata["account_id"] == account_id


def test_publish_request_rejects_cross_platform_account_binding(client, tmp_path):
    cookie_path = tmp_path / "douyin_cookie.json"
    cookie_path.write_text("{}", encoding="utf-8")
    account_resp = client.post(
        "/api/publish/accounts",
        json={"platform": "douyin", "name": "抖音账号", "cookie_path": str(cookie_path)},
    )
    account_id = account_resp.json()["account_id"]

    task = Task(task_id="vf_bind_reject", source_url="https://example.com", source_title="demo")
    task.state = TaskState.READY_TO_PUBLISH.value
    task.products = [{"type": "long_video", "platform": "all", "local_path": "/tmp/video.mp4", "title": "title"}]
    distribute_routes.get_task_store().update(task)

    response = client.post(
        "/api/distribute/publish",
        json={
            "task_id": task.task_id,
            "platforms": ["bilibili"],
            "publish_accounts": {"bilibili": account_id},
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "ACCOUNT_PLATFORM_MISMATCH"


def test_publish_events_api_and_task_detail_include_binding_metadata(client, tmp_path, monkeypatch):
    async def _noop_run_due_jobs(scheduler):
        return None

    monkeypatch.setattr("api.routes.distribute._run_due_jobs", _noop_run_due_jobs)

    cookie_path = tmp_path / "youtube_cookie.json"
    cookie_path.write_text("{}", encoding="utf-8")
    account_resp = client.post(
        "/api/publish/accounts",
        json={"platform": "youtube", "name": "YouTube 主号", "cookie_path": str(cookie_path)},
    )
    account_id = account_resp.json()["account_id"]

    task = Task(task_id="vf_detail_binding", source_url="https://example.com", source_title="demo")
    task.state = TaskState.READY_TO_PUBLISH.value
    task.products = [{"type": "long_video", "platform": "all", "local_path": "/tmp/video.mp4", "title": "title"}]
    distribute_routes.get_task_store().update(task)

    publish_resp = client.post(
        "/api/distribute/publish",
        json={
            "task_id": task.task_id,
            "platforms": ["youtube"],
            "publish_accounts": {"youtube": account_id},
        },
    )
    assert publish_resp.status_code == 200

    detail_resp = client.get(f"/api/tasks/{task.task_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["publish_accounts"] == {"youtube": account_id}
    assert detail["publish_account_details"]["youtube"]["name"] == "YouTube 主号"

    events_resp = client.get(f"/api/distribute/events/{task.task_id}")
    assert events_resp.status_code == 200
    events = events_resp.json()["events"]
    assert any(event["event_type"] == "enqueued" for event in events)


def test_publish_partials_render_account_and_event_context(client, tmp_path, monkeypatch):
    async def _noop_run_due_jobs(scheduler):
        return None

    monkeypatch.setattr("api.routes.distribute._run_due_jobs", _noop_run_due_jobs)

    cookie_path = tmp_path / "bilibili_cookie.json"
    cookie_path.write_text("{}", encoding="utf-8")
    account_resp = client.post(
        "/api/publish/accounts",
        json={"platform": "bilibili", "name": "B站运营号", "cookie_path": str(cookie_path)},
    )
    account_id = account_resp.json()["account_id"]

    task = Task(task_id="vf_publish_partials", source_url="https://example.com", source_title="demo")
    task.state = TaskState.READY_TO_PUBLISH.value
    task.products = [{"type": "long_video", "platform": "all", "local_path": "/tmp/video.mp4", "title": "发布测试"}]
    distribute_routes.get_task_store().update(task)

    publish_resp = client.post(
        "/api/distribute/publish",
        json={
            "task_id": task.task_id,
            "platforms": ["bilibili"],
            "publish_accounts": {"bilibili": account_id},
        },
    )
    assert publish_resp.status_code == 200

    queue_resp = client.get("/web/partials/publish_queue?platform=all")
    assert queue_resp.status_code == 200
    queue_html = queue_resp.text
    assert "B站运营号" in queue_html
    assert "最近事件" in queue_html

    events_resp = client.get(f"/web/partials/publish_events?task_id={task.task_id}")
    assert events_resp.status_code == 200
    assert "enqueued" in events_resp.text
