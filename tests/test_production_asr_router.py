import sys
from pathlib import Path
from typing import Optional

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

    async def transcribe(self, *, video_url: str, video_path: Optional[str], source_lang: str):
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
    def is_router_enabled(self) -> bool:
        return True

    async def transcribe(self, *, video_url: str, video_path: Optional[str], source_lang: str):
        del video_url, video_path, source_lang
        raise RuntimeError("router failed")


class RouterDisabled:
    def is_router_enabled(self) -> bool:
        return False


class RepairPassed:
    passed = True
    repaired = False
    repaired_lines = 0
    zh_line_ratio = 1.0
    unchanged_ratio = 0.0
    message = "ok"


async def _translate_to_zh(texts, target_lang):
    del target_lang
    return [f"ZH:{text}" for text in texts]


async def _repair_pass(task, working_dir):
    del task, working_dir
    return RepairPassed()


class FakeTTSResult:
    def __init__(self, audio_path: str):
        self.audio_path = audio_path


@pytest.mark.asyncio
async def test_step_translate_uses_asr_router_and_generates_artifacts(tmp_path):
    store = TaskStore(store_path=str(tmp_path / "tasks.json"))
    task = store.create(source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    task.state = TaskState.UPLOADING_SOURCE.value
    task.enable_tts = False
    store.update(task)

    pipeline = ProductionPipeline(task_store=store, notifier=NoopNotifier())
    pipeline.asr_router = RouterSuccess()  # type: ignore[assignment]
    pipeline.subtitle_repairer.translate_lines = _translate_to_zh  # type: ignore[method-assign]
    pipeline.subtitle_repairer.repair_if_needed = _repair_pass  # type: ignore[method-assign]

    working_dir = tmp_path / task.task_id
    working_dir.mkdir(parents=True, exist_ok=True)

    ok = await pipeline._step_translate(task, working_dir)

    assert ok is True
    assert task.state == TaskState.QC_CHECKING.value
    assert (working_dir / "origin_language_srt.srt").exists()
    assert (working_dir / "target_language_srt.srt").exists()
    assert (working_dir / "bilingual_srt.srt").exists()
    assert (working_dir / "origin_language.txt").exists()
    assert (working_dir / "target_language.txt").exists()
    assert task.subtitle_path.endswith("bilingual_srt.srt")
    assert task.transcript_text == "ZH:hello world\nZH:second line"
    assert task.translated_title == "ZH:hello world"


@pytest.mark.asyncio
async def test_step_translate_router_failure_marks_task_failed(tmp_path):
    store = TaskStore(store_path=str(tmp_path / "tasks.json"))
    task = store.create(source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    task.state = TaskState.UPLOADING_SOURCE.value
    task.enable_tts = False
    store.update(task)

    pipeline = ProductionPipeline(task_store=store, notifier=NoopNotifier())
    pipeline.asr_router = RouterFail()  # type: ignore[assignment]

    ok = await pipeline._step_translate(task, tmp_path)

    assert ok is False
    assert task.state == TaskState.FAILED.value
    assert task.last_error_code == "ASR_ROUTER_FAILED"


@pytest.mark.asyncio
async def test_step_translate_rejects_legacy_disabled_router(tmp_path):
    store = TaskStore(store_path=str(tmp_path / "tasks.json"))
    task = store.create(source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    task.state = TaskState.UPLOADING_SOURCE.value
    task.enable_tts = False
    store.update(task)

    pipeline = ProductionPipeline(task_store=store, notifier=NoopNotifier())
    pipeline.asr_router = RouterDisabled()  # type: ignore[assignment]

    ok = await pipeline._step_translate(task, tmp_path)

    assert ok is False
    assert task.state == TaskState.FAILED.value
    assert task.last_error_code == "ASR_ROUTER_DISABLED"


@pytest.mark.asyncio
async def test_step_translate_with_tts_stays_in_self_managed_flow(tmp_path):
    store = TaskStore(store_path=str(tmp_path / "tasks.json"))
    task = store.create(source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    task.state = TaskState.UPLOADING_SOURCE.value
    task.enable_tts = True
    source_video = tmp_path / "source_video.mp4"
    source_video.write_bytes(b"x" * 1_500_000)
    task.source_local_path = str(source_video)
    store.update(task)

    pipeline = ProductionPipeline(task_store=store, notifier=NoopNotifier())
    pipeline.asr_router = RouterSuccess()  # type: ignore[assignment]
    pipeline.subtitle_repairer.translate_lines = _translate_to_zh  # type: ignore[method-assign]
    pipeline.subtitle_repairer.repair_if_needed = _repair_pass  # type: ignore[method-assign]

    async def _fake_synthesize(**kwargs):
        output_path = Path(kwargs["output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-audio-bytes" * 800)
        return FakeTTSResult(str(output_path))

    async def _fake_ensure_valid_translated_video(_task, working_dir, **kwargs):
        output_video = working_dir / "output" / "video_with_tts.mp4"
        output_video.parent.mkdir(parents=True, exist_ok=True)
        output_video.write_bytes(b"fake-video" * 200000)
        _task.translated_video_path = str(output_video)
        return True

    pipeline.volcengine_tts.synthesize = _fake_synthesize  # type: ignore[method-assign]
    pipeline._ensure_valid_translated_video = _fake_ensure_valid_translated_video  # type: ignore[method-assign]

    working_dir = tmp_path / task.task_id
    working_dir.mkdir(parents=True, exist_ok=True)

    ok = await pipeline._step_translate(task, working_dir)

    assert ok is True
    assert task.state == TaskState.QC_CHECKING.value
    assert task.tts_audio_path.endswith("tts_final_audio.mp3")
    assert task.translated_video_path.endswith("video_with_tts.mp4")
    assert task.klic_task_id.startswith("selfhosted_")
