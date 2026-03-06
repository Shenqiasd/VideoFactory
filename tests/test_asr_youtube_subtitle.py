import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from asr.youtube_subtitle import YouTubeSubtitleASR  # noqa: E402


def test_extract_video_id_variants():
    assert YouTubeSubtitleASR.extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert YouTubeSubtitleASR.extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert YouTubeSubtitleASR.extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert YouTubeSubtitleASR.extract_video_id("https://example.com/video.mp4") is None


def test_to_srt_formats_blocks():
    items = [
        {"start": 0.0, "duration": 1.2, "text": "hello"},
        {"start": 1.2, "duration": 1.8, "text": "world"},
    ]
    srt = YouTubeSubtitleASR._to_srt(items)  # type: ignore[attr-defined]
    assert "1" in srt
    assert "00:00:00,000 --> 00:00:01,200" in srt
    assert "hello" in srt
    assert "world" in srt


@pytest.mark.asyncio
async def test_transcribe_uses_fetch_result(monkeypatch):
    provider = YouTubeSubtitleASR()

    def _fake_fetch(video_id: str, source_lang: str):
        assert video_id == "dQw4w9WgXcQ"
        assert source_lang == "en"
        return [{"start": 0.0, "duration": 1.0, "text": "line one"}]

    monkeypatch.setattr(provider, "_fetch_transcript_items", _fake_fetch)

    result = await provider.transcribe(
        video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        video_path=None,
        source_lang="en",
    )

    assert result is not None
    assert result.method == "youtube"
    assert "line one" in result.srt_content

