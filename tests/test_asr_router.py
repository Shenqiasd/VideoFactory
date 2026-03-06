import sys
from pathlib import Path
from typing import Optional

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from asr import ASRResult, ASRRouter  # noqa: E402


class _DummyProvider:
    def __init__(self, name: str, result: Optional[ASRResult]):
        self.name = name
        self.result = result
        self.calls = 0

    async def transcribe(self, *, video_url: str, video_path: Optional[str], source_lang: str):
        self.calls += 1
        return self.result


@pytest.mark.asyncio
async def test_asr_router_fallback_order_hits_next_provider():
    router = ASRRouter()
    router.provider = "auto"
    router.fallback_order = ["youtube", "volcengine", "whisper"]

    youtube = _DummyProvider("youtube", None)
    volc = _DummyProvider("volcengine", None)
    whisper = _DummyProvider(
        "whisper",
        ASRResult(srt_content="1\n00:00:00,000 --> 00:00:01,000\nhello\n", method="whisper"),
    )
    router.providers = {
        "youtube": youtube,       # type: ignore[assignment]
        "volcengine": volc,       # type: ignore[assignment]
        "whisper": whisper,       # type: ignore[assignment]
    }

    result = await router.transcribe(
        video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        video_path=None,
        source_lang="en",
    )

    assert result.method == "whisper"
    assert youtube.calls == 1
    assert volc.calls == 1
    assert whisper.calls == 1


@pytest.mark.asyncio
async def test_asr_router_skips_youtube_for_non_youtube_url():
    router = ASRRouter()
    router.provider = "auto"
    router.fallback_order = ["youtube", "whisper"]

    youtube = _DummyProvider("youtube", ASRResult(srt_content="x", method="youtube"))
    whisper = _DummyProvider(
        "whisper",
        ASRResult(srt_content="1\n00:00:00,000 --> 00:00:01,000\nok\n", method="whisper"),
    )
    router.providers = {
        "youtube": youtube,       # type: ignore[assignment]
        "whisper": whisper,       # type: ignore[assignment]
    }

    result = await router.transcribe(
        video_url="/tmp/local.mp4",
        video_path="/tmp/local.mp4",
        source_lang="en",
    )

    assert result.method == "whisper"
    assert youtube.calls == 0
    assert whisper.calls == 1

