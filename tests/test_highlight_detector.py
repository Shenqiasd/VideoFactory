import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core.task import Task  # noqa: E402
from creation.highlight_detector import HighlightDetector  # noqa: E402
from creation.models import HighlightSegment  # noqa: E402
from creation.pipeline import CreationPipeline  # noqa: E402


@pytest.mark.asyncio
async def test_highlight_detector_semantic_strategy_skips_scene_and_audio(monkeypatch, tmp_path):
    subtitle = tmp_path / 'sample.srt'
    subtitle.write_text(
        '1\n00:00:00,000 --> 00:00:20,000\n这是一个重点讲解片段。\n\n'
        '2\n00:00:25,000 --> 00:00:50,000\n接下来继续展开说明。\n',
        encoding='utf-8',
    )

    detector = HighlightDetector()

    def _fail_scene(_video_path):
        raise AssertionError('scene detection should not run for semantic strategy')

    def _fail_audio(_candidates, _audio_source_path):
        raise AssertionError('audio scoring should not run for semantic strategy')

    monkeypatch.setattr(detector, '_detect_scene_boundaries', _fail_scene)
    monkeypatch.setattr(detector, '_score_with_audio', _fail_audio)

    segments = await detector.detect(
        'demo.mp4',
        str(subtitle),
        clip_count=1,
        min_duration=10,
        max_duration=60,
        strategy='semantic',
    )

    assert len(segments) == 1
    assert segments[0].semantic_score > 0
    assert segments[0].audio_score == 0
    assert segments[0].scene_score == 0
    assert segments[0].total_score > 0


@pytest.mark.asyncio
async def test_creation_pipeline_legacy_strategy_bypasses_highlight_detector(monkeypatch, tmp_path):
    source_video = tmp_path / 'translated.mp4'
    subtitle = tmp_path / 'bilingual.srt'
    source_video.write_bytes(b'0' * 1_200_000)
    subtitle.write_text('1\n00:00:00,000 --> 00:00:02,000\n你好\n', encoding='utf-8')

    pipeline = CreationPipeline(task_store=None)
    task = Task(
        source_url='https://example.com/video',
        translated_video_path=str(source_video),
        subtitle_path=str(subtitle),
        enable_short_clips=True,
        creation_config={
            'highlight_strategy': 'legacy',
            'platforms': ['douyin'],
            'clip_count': 1,
            'duration_min': 10,
            'duration_max': 30,
            'review_mode': 'none',
        },
    )

    async def _fail_detect(*args, **kwargs):
        raise AssertionError('highlight detector should be bypassed for legacy strategy')

    async def _fake_fallback(*args, **kwargs):
        return [
            HighlightSegment(
                segment_id='fallback_001',
                start=0.0,
                end=12.0,
                title='Fallback',
                summary='legacy fallback segment',
                total_score=0.3,
                semantic_score=0.3,
            )
        ]

    async def _fake_extract_video(video_path, start, end, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b'1' * 1_200_000)
        return True

    def _fake_extract_subtitles(subtitle_path, start, end, output_path):
        Path(output_path).write_text('1\n00:00:00,000 --> 00:00:02,000\n你好\n', encoding='utf-8')
        return output_path

    async def _fake_subject_detect(_video_path):
        return {'strategy': 'center', 'focus_class': 'center', 'samples': []}

    async def _fake_crop(video_path, output_path, focus_track, *, aspect_ratio='9:16', target_size=(1080, 1920)):
        Path(output_path).write_bytes(b'2' * 1_200_000)
        return True, {'strategy': 'center', 'focus_class': 'center', 'samples': []}

    async def _fake_render(task, segment, profile, output_dir):
        out = Path(output_dir) / 'douyin.mp4'
        out.write_bytes(b'3' * 1_200_000)
        from creation.models import RenderVariant
        return RenderVariant(
            segment_id=segment.segment_id,
            platform='douyin',
            profile='vertical_knowledge',
            title='Fallback · douyin',
            local_path=str(out),
        )

    monkeypatch.setattr(pipeline.highlight_detector, 'detect', _fail_detect)
    monkeypatch.setattr(pipeline, '_fallback_segments', _fake_fallback)
    monkeypatch.setattr(pipeline.clip_extractor, 'extract_video', _fake_extract_video)
    monkeypatch.setattr(pipeline.clip_extractor, 'extract_subtitles', _fake_extract_subtitles)
    monkeypatch.setattr(pipeline.subject_detector, 'detect', _fake_subject_detect)
    monkeypatch.setattr(pipeline.smart_cropper, 'crop', _fake_crop)
    monkeypatch.setattr(pipeline, '_render_variant', _fake_render)

    result = await pipeline.process(
        task,
        video_path=str(source_video),
        subtitle_path=str(subtitle),
        output_dir=str(tmp_path / 'creation'),
    )

    assert result.used_fallback is True
    assert len(result.segments) == 1
    assert result.segments[0].segment_id == 'fallback_001'
