import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from factory.long_video import LongVideoProcessor  # noqa: E402


@pytest.mark.asyncio
async def test_burn_subtitles_preview_mode_disables_soft_fallback(tmp_path, monkeypatch):
    processor = LongVideoProcessor(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")

    subtitle_path = tmp_path / "sample.srt"
    subtitle_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\nhello\n",
        encoding="utf-8",
    )
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    output_path = tmp_path / "out.mp4"

    calls = []

    async def fake_run_ffmpeg(args, timeout=600):
        calls.append(args)
        return False

    def fake_generate_ass(*, srt_path, ass_path, style, font_name, render_width=1920, render_height=1080):
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write("[Script Info]\n")

    monkeypatch.setattr(processor, "_run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(processor, "_generate_ass_from_srt", fake_generate_ass)

    ok = await processor.burn_subtitles(
        video_path=str(video_path),
        subtitle_path=str(subtitle_path),
        output_path=str(output_path),
        allow_soft_fallback=False,
    )

    assert ok is False
    assert len(calls) == 1
    assert "ass=" in calls[0][3]


@pytest.mark.asyncio
async def test_burn_subtitles_keeps_soft_fallback_for_pipeline(tmp_path, monkeypatch):
    processor = LongVideoProcessor(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")

    subtitle_path = tmp_path / "sample.srt"
    subtitle_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\nhello\n",
        encoding="utf-8",
    )
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    output_path = tmp_path / "out.mp4"

    calls = []

    async def fake_run_ffmpeg(args, timeout=600):
        calls.append(args)
        return len(calls) == 2

    def fake_generate_ass(*, srt_path, ass_path, style, font_name, render_width=1920, render_height=1080):
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write("[Script Info]\n")

    monkeypatch.setattr(processor, "_run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(processor, "_generate_ass_from_srt", fake_generate_ass)

    ok = await processor.burn_subtitles(
        video_path=str(video_path),
        subtitle_path=str(subtitle_path),
        output_path=str(output_path),
        allow_soft_fallback=True,
    )

    assert ok is True
    assert len(calls) == 2
    assert "ass=" in calls[0][3]
    assert calls[1][0:4] == ["-i", str(video_path), "-i", str(subtitle_path)]


@pytest.mark.asyncio
async def test_burn_subtitles_with_debug_fails_when_visibility_too_low(tmp_path, monkeypatch):
    processor = LongVideoProcessor(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")

    subtitle_path = tmp_path / "sample.srt"
    subtitle_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\nhello\n",
        encoding="utf-8",
    )
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    output_path = tmp_path / "out.mp4"

    async def fake_run_ffmpeg(args, timeout=600):
        return True

    async def fake_get_video_info(path):
        return {"width": 1280, "height": 720}

    async def fake_visibility(*args, **kwargs):
        return 0.0

    def fake_generate_ass(*, srt_path, ass_path, style, font_name, render_width=1920, render_height=1080):
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write("[Script Info]\n")

    monkeypatch.setattr(processor, "_run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(processor, "get_video_info", fake_get_video_info)
    monkeypatch.setattr(processor, "_calculate_visibility_score", fake_visibility)
    monkeypatch.setattr(processor, "_generate_ass_from_srt", fake_generate_ass)

    ok, debug = await processor.burn_subtitles_with_debug(
        video_path=str(video_path),
        subtitle_path=str(subtitle_path),
        output_path=str(output_path),
        allow_soft_fallback=False,
        visibility_check=True,
        visibility_threshold=0.002,
    )

    assert ok is False
    assert "可见性校验" in debug.get("error", "")
