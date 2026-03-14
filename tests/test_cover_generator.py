import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from factory.cover import CoverGenerator  # noqa: E402


@pytest.mark.asyncio
async def test_process_prefers_youtube_thumbnail_and_outputs_single_cover(monkeypatch, tmp_path):
    generator = CoverGenerator()
    output_dir = tmp_path / "covers"

    async def _fake_download_source_thumbnail(self, source_url: str, output_dir: str):
        del self, source_url
        cover_path = Path(output_dir) / "cover_horizontal.jpg"
        cover_path.parent.mkdir(parents=True, exist_ok=True)
        cover_path.write_bytes(b"thumbnail-bytes")
        return str(cover_path)

    async def _should_not_extract(*args, **kwargs):
        raise AssertionError("frame extraction should not run when source thumbnail is available")

    monkeypatch.setattr(
        CoverGenerator,
        "_download_source_thumbnail",
        _fake_download_source_thumbnail,
        raising=False,
    )
    monkeypatch.setattr(CoverGenerator, "extract_keyframes", _should_not_extract)

    result = await generator.process(
        video_path=str(tmp_path / "video.mp4"),
        output_dir=str(output_dir),
        source_url="https://www.youtube.com/watch?v=abc123xyz89",
    )

    assert result == {"horizontal": str(output_dir / "cover_horizontal.jpg")}
    assert sorted(path.name for path in output_dir.iterdir() if path.is_file()) == ["cover_horizontal.jpg"]


@pytest.mark.asyncio
async def test_process_frame_fallback_keeps_only_final_cover(monkeypatch, tmp_path):
    generator = CoverGenerator()
    output_dir = tmp_path / "covers"

    async def _no_source_thumbnail(self, source_url: str, output_dir: str):
        del self, source_url, output_dir
        return None

    async def _fake_extract(self, video_path: str, output_dir: str, count: int = 5, format: str = "jpg", quality: int = 2):
        del self, video_path, count, format, quality
        frame_dir = Path(output_dir)
        frame_dir.mkdir(parents=True, exist_ok=True)
        first = frame_dir / "cover_01.jpg"
        second = frame_dir / "cover_02.jpg"
        first.write_bytes(b"frame-1")
        second.write_bytes(b"frame-2")
        return [str(first), str(second)]

    async def _fake_select(self, frame_paths):
        del self
        return frame_paths[0]

    async def _fake_create_horizontal(self, frame_path: str, output_path: str, width: int = 1920, height: int = 1080):
        del self, frame_path, width, height
        cover_path = Path(output_path)
        cover_path.parent.mkdir(parents=True, exist_ok=True)
        cover_path.write_bytes(b"final-cover")
        return True

    monkeypatch.setattr(
        CoverGenerator,
        "_download_source_thumbnail",
        _no_source_thumbnail,
        raising=False,
    )
    monkeypatch.setattr(CoverGenerator, "extract_keyframes", _fake_extract)
    monkeypatch.setattr(CoverGenerator, "select_best_frame", _fake_select)
    monkeypatch.setattr(CoverGenerator, "create_horizontal_cover", _fake_create_horizontal)

    result = await generator.process(
        video_path=str(tmp_path / "video.mp4"),
        output_dir=str(output_dir),
        source_url="https://example.com/video",
    )

    assert result == {"horizontal": str(output_dir / "cover_horizontal.jpg")}
    assert sorted(path.name for path in output_dir.iterdir() if path.is_file()) == ["cover_horizontal.jpg"]


@pytest.mark.asyncio
async def test_process_generates_vertical_cover_when_enabled(monkeypatch, tmp_path):
    generator = CoverGenerator()
    output_dir = tmp_path / "covers"

    async def _no_source_thumbnail(self, source_url: str, output_dir: str):
        del self, source_url, output_dir
        return None

    async def _fake_extract(self, video_path: str, output_dir: str, count: int = 5, format: str = "jpg", quality: int = 2):
        del self, video_path, count, format, quality
        frame_dir = Path(output_dir)
        frame_dir.mkdir(parents=True, exist_ok=True)
        first = frame_dir / "cover_01.jpg"
        first.write_bytes(b"frame-1")
        return [str(first)]

    async def _fake_select(self, frame_paths):
        del self
        return frame_paths[0]

    async def _fake_create_horizontal(self, frame_path: str, output_path: str, width: int = 1920, height: int = 1080):
        del self, frame_path, width, height
        Path(output_path).write_bytes(b"horizontal")
        return True

    async def _fake_create_vertical(self, frame_path: str, output_path: str, width: int = 1080, height: int = 1920):
        del self, frame_path, width, height
        Path(output_path).write_bytes(b"vertical")
        return True

    monkeypatch.setattr(CoverGenerator, "_download_source_thumbnail", _no_source_thumbnail, raising=False)
    monkeypatch.setattr(CoverGenerator, "extract_keyframes", _fake_extract)
    monkeypatch.setattr(CoverGenerator, "select_best_frame", _fake_select)
    monkeypatch.setattr(CoverGenerator, "create_horizontal_cover", _fake_create_horizontal)
    monkeypatch.setattr(CoverGenerator, "create_vertical_cover", _fake_create_vertical)

    result = await generator.process(
        video_path=str(tmp_path / "video.mp4"),
        output_dir=str(output_dir),
        generate_vertical=True,
        source_url="https://example.com/video",
    )

    assert result == {
        "horizontal": str(output_dir / "cover_horizontal.jpg"),
        "vertical": str(output_dir / "cover_vertical.jpg"),
    }
    assert sorted(path.name for path in output_dir.iterdir() if path.is_file()) == ["cover_horizontal.jpg", "cover_vertical.jpg"]
