import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from asr import ASRResult  # noqa: E402
from core.task import TaskState, TaskStore  # noqa: E402
from production.pipeline import ProductionPipeline  # noqa: E402


class NoopNotifier:
    async def notify_task_state_change(self, *args, **kwargs):
        return None

    async def notify(self, *args, **kwargs):
        return None

    async def notify_error(self, *args, **kwargs):
        return None

    async def close(self):
        return None


class RouterSuccess:
    allow_klicstudio_fallback = True

    def is_router_enabled(self) -> bool:
        return True

    async def transcribe(self, *, video_url: str, video_path: str | None, source_lang: str):
        del video_url, video_path, source_lang
        return ASRResult(
            srt_content=(
                "1\n00:00:00,000 --> 00:00:01,500\nhello world\n\n"
                "2\n00:00:01,500 --> 00:00:03,000\nsecond line\n"
            ),
            method="youtube",
            source_lang="en",
        )


class RouterFail:
    allow_klicstudio_fallback = True

    def is_router_enabled(self) -> bool:
        return True

    async def transcribe(self, *, video_url: str, video_path: str | None, source_lang: str):
        del video_url, video_path, source_lang
        raise RuntimeError("router failed")


@pytest.mark.asyncio
async def test_step_translate_uses_asr_router_and_generates_srt(tmp_path):
    store = TaskStore(store_path=str(tmp_path / "tasks.json"))
    task = store.create(source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    task.state = TaskState.UPLOADING_SOURCE.value
    task.enable_tts = False
    store.update(task)

    pipeline = ProductionPipeline(task_store=store, notifier=NoopNotifier())
    pipeline.asr_router = RouterSuccess()  # type: ignore[assignment]

    working_dir = tmp_path / task.task_id
    working_dir.mkdir(parents=True, exist_ok=True)

    ok = await pipeline._step_translate(task, working_dir)

    assert ok is True
    assert task.state == TaskState.QC_CHECKING.value
    assert (working_dir / "origin_language_srt.srt").exists()
    assert (working_dir / "target_language_srt.srt").exists()
    assert (working_dir / "bilingual_srt.srt").exists()
    assert task.subtitle_path.endswith("bilingual_srt.srt")


@pytest.mark.asyncio
async def test_step_translate_router_failure_falls_back_to_klic(monkeypatch, tmp_path):
    store = TaskStore(store_path=str(tmp_path / "tasks.json"))
    task = store.create(source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    task.state = TaskState.UPLOADING_SOURCE.value
    task.enable_tts = False
    store.update(task)

    pipeline = ProductionPipeline(task_store=store, notifier=NoopNotifier())
    pipeline.asr_router = RouterFail()  # type: ignore[assignment]

    async def _fake_submit(_task, video_url: str):
        assert video_url.startswith("https://")
        return None, "All connection attempts failed"

    monkeypatch.setattr(pipeline, "_submit_klic_task_with_retry", _fake_submit)

    ok = await pipeline._step_translate(task, tmp_path)

    assert ok is False
    assert task.state == TaskState.FAILED.value
    assert task.last_error_code == "KLIC_UNAVAILABLE"

