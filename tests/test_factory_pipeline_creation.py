import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.task import Task, TaskState  # noqa: E402
from creation.models import CreationResult, HighlightSegment, RenderVariant  # noqa: E402
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
async def test_factory_pipeline_records_creation_variants_and_pending_review(monkeypatch, tmp_path):
    source_video = tmp_path / "translated.mp4"
    subtitle = tmp_path / "bilingual.srt"
    source_video.write_bytes(b"0" * 1_200_000)
    subtitle.write_text("1\n00:00:00,000 --> 00:00:02,000\n你好\nhello\n", encoding="utf-8")

    pipeline = FactoryPipeline(
        task_store=_DummyStore(),
        storage=SimpleNamespace(sync_to_r2=lambda *args, **kwargs: True),
        local_storage=_DummyLocalStorage(tmp_path / "working", tmp_path / "output"),
        notifier=_DummyNotifier(),
    )

    async def _fake_long_video(task, video_path, subtitle_path, output_dir):
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        result = out_dir / "long_video.mp4"
        result.write_bytes(b"1" * 1_200_000)
        return str(result)

    async def _fake_creation_process(task, *, video_path, subtitle_path, output_dir):
        variant_path = Path(output_dir) / "seg_001" / "douyin.mp4"
        variant_path.parent.mkdir(parents=True, exist_ok=True)
        variant_path.write_bytes(b"2" * 1_200_000)
        task.update_creation_state(
            status="completed",
            stage="completed",
            review_status="pending",
            selected_segments=[
                {
                    "segment_id": "seg_001",
                    "title": "知识点片段 1",
                    "start": 0.0,
                    "end": 60.0,
                    "duration": 60.0,
                }
            ],
            segments_total=1,
            segments_completed=1,
            variants_total=1,
            variants_completed=1,
            used_fallback=False,
        )
        return CreationResult(
            review_required=True,
            review_status="pending",
            segments=[
                HighlightSegment(
                    segment_id="seg_001",
                    start=0.0,
                    end=60.0,
                    title="知识点片段 1",
                    total_score=0.91,
                )
            ],
            variants=[
                RenderVariant(
                    segment_id="seg_001",
                    platform="douyin",
                    profile="vertical_knowledge",
                    title="知识点片段 1 · douyin",
                    local_path=str(variant_path),
                    metadata={"review_status": "pending", "segment_id": "seg_001"},
                )
            ],
            stats={"segments_total": 1, "variants_total": 1},
        )

    async def _fake_covers(task, video_path, output_dir):
        return {}

    async def _fake_metadata(task, transcript):
        return {"douyin": {"hashtags": ["a"]}}

    async def _fake_articles(task, transcript, output_dir):
        return {}

    monkeypatch.setattr(pipeline, "_process_long_video", _fake_long_video)
    monkeypatch.setattr(pipeline.creation, "process", _fake_creation_process)
    monkeypatch.setattr(pipeline, "_process_covers", _fake_covers)
    monkeypatch.setattr(pipeline, "_process_metadata", _fake_metadata)
    monkeypatch.setattr(pipeline, "_process_articles", _fake_articles)

    task = Task(
        source_url=str(source_video),
        state=TaskState.QC_PASSED.value,
        task_scope="full",
        translated_video_path=str(source_video),
        subtitle_path=str(subtitle),
        translated_title="测试标题",
    )

    ok = await pipeline.run(task)
    assert ok is True
    assert task.state == TaskState.READY_TO_PUBLISH.value
    assert task.progress == 88
    short_products = [product for product in task.products if product.get("type") == "short_clip"]
    assert len(short_products) == 1
    assert short_products[0]["platform"] == "douyin"
    assert short_products[0]["metadata"]["segment_id"] == "seg_001"
    assert task.creation_status["review_required"] is True
    assert task.creation_status["review_status"] == "pending"


@pytest.mark.asyncio
async def test_factory_pipeline_does_not_require_review_without_short_clip_outputs(monkeypatch, tmp_path):
    source_video = tmp_path / "translated.mp4"
    subtitle = tmp_path / "bilingual.srt"
    source_video.write_bytes(b"0" * 1_200_000)
    subtitle.write_text("1\n00:00:00,000 --> 00:00:02,000\n你好\nhello\n", encoding="utf-8")

    pipeline = FactoryPipeline(
        task_store=_DummyStore(),
        storage=SimpleNamespace(sync_to_r2=lambda *args, **kwargs: True),
        local_storage=_DummyLocalStorage(tmp_path / "working", tmp_path / "output"),
        notifier=_DummyNotifier(),
    )

    async def _fake_long_video(task, video_path, subtitle_path, output_dir):
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        result = out_dir / "long_video.mp4"
        result.write_bytes(b"1" * 1_200_000)
        return str(result)

    async def _fake_creation_process(task, *, video_path, subtitle_path, output_dir):
        task.update_creation_state(
            status="completed",
            stage="completed",
            review_status="pending",
            selected_segments=[
                {
                    "segment_id": "seg_001",
                    "title": "知识点片段 1",
                    "start": 0.0,
                    "end": 60.0,
                    "duration": 60.0,
                }
            ],
            segments_total=1,
            segments_completed=1,
            variants_total=1,
            variants_completed=0,
            used_fallback=False,
        )
        return CreationResult(
            review_required=False,
            review_status="not_required",
            segments=[
                HighlightSegment(
                    segment_id="seg_001",
                    start=0.0,
                    end=60.0,
                    title="知识点片段 1",
                    total_score=0.91,
                )
            ],
            variants=[],
            stats={"segments_total": 1, "variants_total": 1, "variants_completed": 0},
        )

    async def _fake_covers(task, video_path, output_dir):
        return {}

    async def _fake_metadata(task, transcript):
        return {"douyin": {"hashtags": ["a"]}}

    async def _fake_articles(task, transcript, output_dir):
        return {}

    monkeypatch.setattr(pipeline, "_process_long_video", _fake_long_video)
    monkeypatch.setattr(pipeline.creation, "process", _fake_creation_process)
    monkeypatch.setattr(pipeline, "_process_covers", _fake_covers)
    monkeypatch.setattr(pipeline, "_process_metadata", _fake_metadata)
    monkeypatch.setattr(pipeline, "_process_articles", _fake_articles)

    task = Task(
        source_url=str(source_video),
        state=TaskState.QC_PASSED.value,
        task_scope="full",
        translated_video_path=str(source_video),
        subtitle_path=str(subtitle),
        translated_title="测试标题",
    )

    ok = await pipeline.run(task)
    assert ok is True
    assert task.state == TaskState.READY_TO_PUBLISH.value
    assert task.progress == 90
    assert [product for product in task.products if product.get("type") == "short_clip"] == []
    assert task.creation_status["review_required"] is False
    assert task.creation_status["review_status"] == "pending"


@pytest.mark.asyncio
async def test_record_products_truncates_long_video_description_and_records_cover(tmp_path):
    pipeline = FactoryPipeline(
        task_store=_DummyStore(),
        storage=SimpleNamespace(sync_to_r2=lambda *args, **kwargs: True),
        local_storage=_DummyLocalStorage(tmp_path / "working", tmp_path / "output"),
        notifier=_DummyNotifier(),
    )

    long_video = tmp_path / "long_video.mp4"
    cover = tmp_path / "horizontal.png"
    vertical_cover = tmp_path / "vertical.png"
    long_video.write_bytes(b"1" * 1_200_000)
    cover.write_bytes(b"cover")
    vertical_cover.write_bytes(b"vertical-cover")

    task = Task(
        source_url="https://example.com/video",
        state=TaskState.PROCESSING.value,
        translated_title="规范项目名",
        translated_description="备用简介 " * 80,
    )

    await pipeline._record_products(
        task,
        str(long_video),
        CreationResult(),
        {"horizontal": str(cover), "vertical": str(vertical_cover)},
        {"bilibili": {"description": "平台简介 " * 80}},
    )

    long_video_products = [product for product in task.products if product.get("type") == "long_video"]
    cover_products = [product for product in task.products if product.get("type") == "cover"]

    assert len(long_video_products) == 1
    assert len(cover_products) == 2
    assert len(long_video_products[0]["description"]) <= 200
    assert long_video_products[0]["description"] == (" ".join(("平台简介 " * 80).split()))[:200]
    assert sorted(product["metadata"]["cover_type"] for product in cover_products) == ["horizontal", "vertical"]
