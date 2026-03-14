import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.task import Task, TaskState  # noqa: E402
from factory.pipeline import FactoryPipeline  # noqa: E402


class _DummyStore:
    def update(self, task):
        return None


class _DummyLocalStorage:
    def __init__(self, working_root: Path, output_root: Path):
        self.working_root = working_root
        self.output_root = output_root

    def get_task_working_dir(self, task_id: str) -> Path:
        path = self.working_root / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_task_output_dir(self, task_id: str) -> Path:
        path = self.output_root / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path


class _DummyNotifier:
    async def notify_task_state_change(self, *args, **kwargs):
        return None

    async def notify(self, *args, **kwargs):
        return None

    async def notify_error(self, *args, **kwargs):
        return None


@pytest.mark.asyncio
async def test_subtitle_only_falls_back_to_source_video(monkeypatch, tmp_path):
    source_video = tmp_path / "external_source.mp4"
    source_video.write_bytes(b"0" * 1_200_000)

    subtitle = tmp_path / "bilingual_srt.srt"
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\nhello\n",
        encoding="utf-8",
    )

    pipeline = FactoryPipeline(
        task_store=_DummyStore(),
        storage=SimpleNamespace(),
        local_storage=_DummyLocalStorage(tmp_path / "working", tmp_path / "output"),
        notifier=_DummyNotifier(),
    )

    captured = {}

    async def _fake_process_long_video(task, video_path, subtitle_path, output_dir):
        captured["video_path"] = video_path
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "long_video.mp4"
        out_file.write_bytes(b"1" * 1_200_000)
        return str(out_file)

    async def _fake_process_covers(task, video_path, output_dir):
        captured["cover_video_path"] = video_path
        out_dir = Path(output_dir) / "covers"
        out_dir.mkdir(parents=True, exist_ok=True)
        cover_file = out_dir / "horizontal.png"
        cover_file.write_bytes(b"cover")
        return {"horizontal": str(cover_file)}

    async def _fake_process_metadata(task, transcript):
        captured["transcript"] = transcript
        return {"bilibili": {"description": "字幕简介" * 80}}

    async def _fake_record_products(task, long_video_path, clip_paths, cover_paths, metadata_map):
        captured["recorded_long_video_path"] = long_video_path
        captured["recorded_clip_paths"] = clip_paths
        captured["recorded_cover_paths"] = cover_paths
        captured["recorded_metadata_map"] = metadata_map
        return None

    async def _fake_upload_products(task, output_dir):
        return None

    monkeypatch.setattr(pipeline, "_process_long_video", _fake_process_long_video)
    monkeypatch.setattr(pipeline, "_process_covers", _fake_process_covers)
    monkeypatch.setattr(pipeline, "_process_metadata", _fake_process_metadata)
    monkeypatch.setattr(pipeline, "_record_products", _fake_record_products)
    monkeypatch.setattr(pipeline, "_upload_products", _fake_upload_products)

    task = Task(
        source_url=str(source_video),
        state=TaskState.QC_PASSED.value,
        task_scope="subtitle_only",
        enable_tts=False,
        source_local_path=str(source_video),
        subtitle_path=str(subtitle),
        transcript_text="subtitle only transcript",
    )

    ok = await pipeline.run(task)
    assert ok is True
    assert captured["video_path"] == str(source_video)
    assert captured["cover_video_path"] == str(source_video)
    assert captured["transcript"] == "subtitle only transcript"
    assert captured["recorded_long_video_path"].endswith("long_video.mp4")
    assert captured["recorded_clip_paths"]["variants"] == []
    assert captured["recorded_cover_paths"]["horizontal"].endswith("horizontal.png")
    assert captured["recorded_metadata_map"]["bilibili"]["description"].startswith("字幕简介")
