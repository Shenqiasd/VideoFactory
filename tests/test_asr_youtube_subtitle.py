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


def test_parse_srv3_entries_removes_rolling_duplicates():
    content = """<?xml version="1.0" encoding="utf-8" ?>
<timedtext format="3">
<body>
<p t="15519" d="4241"><s>for</s><s t="281"> years</s><s t="1281"> it's</s><s t="1441"> one</s><s t="1600"> that</s><s t="1760"> a</s><s t="1881"> lot</s><s t="2001"> of</s><s t="2161"> music</s></p>
<p t="17960" d="4960"><s>fans</s><s t="280"> have</s><s t="479"> asked</s><s t="800"> and</s><s t="1079"> a</s><s t="1200"> lot</s><s t="1319"> of</s><s t="1399"> the</s><s t="1520"> music</s></p>
<p t="19760" d="5439"><s>press</s><s t="320"> have</s><s t="560"> asked</s><s t="1480"> that</s><s t="1720"> question</s><s t="2279"> is</s><s t="2960"> what</s></p>
</body>
</timedtext>
"""
    entries = YouTubeSubtitleASR._parse_srv3_entries(content)  # type: ignore[attr-defined]

    assert [entry["text"] for entry in entries] == [
        "for years it's one that a lot of music",
        "fans have asked and a lot of the music",
        "press have asked that question is what",
    ]


def test_parse_text_cue_entries_removes_rolling_duplicates():
    content = """15
00:00:15,519 --> 00:00:17,950
one that I've asked myself on and off
for years it's one that a lot of music

16
00:00:17,950 --> 00:00:17,960
for years it's one that a lot of music

17
00:00:17,960 --> 00:00:19,750
for years it's one that a lot of music
fans have asked and a lot of the music

18
00:00:19,750 --> 00:00:19,760
fans have asked and a lot of the music

19
00:00:19,760 --> 00:00:22,910
fans have asked and a lot of the music
press have asked that question is what
"""
    entries = YouTubeSubtitleASR._parse_text_cue_entries(content)  # type: ignore[attr-defined]

    assert [entry["text"] for entry in entries] == [
        "one that I've asked myself on and off for years it's one that a lot of music",
        "fans have asked and a lot of the music",
        "press have asked that question is what",
    ]
    assert entries[0]["end"] <= entries[1]["start"]
    assert entries[1]["end"] <= entries[2]["start"]


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


@pytest.mark.asyncio
async def test_transcribe_falls_back_to_ytdlp_subtitles(monkeypatch):
    provider = YouTubeSubtitleASR()

    def _fake_fetch(video_id: str, source_lang: str):
        assert video_id == "dQw4w9WgXcQ"
        return []

    async def _fake_download(video_url: str, source_lang: str):
        assert video_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert source_lang == "en"
        return "1\n00:00:00,000 --> 00:00:01,000\nfallback line\n"

    monkeypatch.setattr(provider, "_fetch_transcript_items", _fake_fetch)
    monkeypatch.setattr(provider, "_download_subtitles_via_ytdlp", _fake_download)

    result = await provider.transcribe(
        video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        video_path=None,
        source_lang="en",
    )

    assert result is not None
    assert result.method == "youtube"
    assert "fallback line" in result.srt_content
    assert result.metadata["source"] == "yt-dlp"
