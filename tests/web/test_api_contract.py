from api.routes import tasks as tasks_routes
from api.routes import distribute as distribute_routes
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


def test_task_artifacts_list_and_download(client, tmp_path):
    source_file = tmp_path / "source_video.mp4"
    subtitle_file = tmp_path / "bilingual_srt.srt"
    source_file.write_bytes(b"fake-video")
    subtitle_file.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")

    created = client.post(
        "/api/tasks/",
        json={"source_url": "https://www.youtube.com/watch?v=artifact_case"},
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

    download_resp = client.get(artifact["download_url"])
    assert download_resp.status_code == 200
    assert download_resp.content == b"from-r2"


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
                "allow_klicstudio_fallback": False,
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
                "fallback_order": ["volcengine", "klicstudio"],
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
                "volcengine_ark": {
                    "enabled": True,
                    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                    "api_key": "ark_test_key",
                    "model": "doubao-seed-translation",
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
    assert payload["tts"]["provider"] == "volcengine"
    assert payload["translation"]["provider"] == "volcengine_ark"

    get_resp2 = client.get("/api/system/settings/asr-tts")
    assert get_resp2.status_code == 200
    assert get_resp2.json()["asr"]["provider"] == "youtube"
    assert get_resp2.json()["tts"]["provider"] == "volcengine"
    assert get_resp2.json()["translation"]["provider"] == "volcengine_ark"

    persisted = yaml.safe_load(test_config_path.read_text(encoding="utf-8"))
    assert persisted["asr"]["provider"] == "youtube"
    assert persisted["tts"]["provider"] == "volcengine"
    assert persisted["tts"]["volcengine"]["default_voice"] == "BV001_streaming"
    assert persisted["translation"]["provider"] == "volcengine_ark"


def test_asr_tts_settings_rejects_invalid_provider(client):
    response = client.post(
        "/api/system/settings/asr-tts",
        json={
            "asr": {
                "provider": "invalid_provider",
                "allow_fallback": True,
                "allow_klicstudio_fallback": True,
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
                "provider": "klicstudio",
                "fallback_order": ["volcengine", "klicstudio"],
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
            "model": "doubao-seed-translation",
            "timeout": 30,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["provider"] == "volcengine_ark"
    assert payload["result"] == "Hello runtime-override"


def test_system_tts_test_endpoint(client, monkeypatch, tmp_path):
    from core.config import Config
    from tts.base import TTSResult
    import yaml

    src_config_path = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
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
    cookie_a.write_text("{}", encoding="utf-8")
    cookie_b = tmp_path / "douyin_b.json"
    cookie_b.write_text("{}", encoding="utf-8")

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
