import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from production.pipeline import QualityChecker  # noqa: E402


def _write_srt(path: Path, lines: list[str]):
    blocks = []
    for i, line in enumerate(lines, start=1):
        blocks.append(
            f"{i}\n00:00:0{i},000 --> 00:00:0{i},800\n{line}"
        )
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def _write_custom_srt(path: Path, blocks: list[tuple[str, str, str]]):
    raw = []
    for i, (start, end, line) in enumerate(blocks, start=1):
        raw.append(f"{i}\n{start} --> {end}\n{line}")
    path.write_text("\n\n".join(raw) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_quality_checker_flags_english_residue(tmp_path):
    working_dir = tmp_path / "task"
    working_dir.mkdir(parents=True, exist_ok=True)
    (working_dir / "output").mkdir(parents=True, exist_ok=True)
    (working_dir / "output" / "video.mp4").write_bytes(b"x" * 1_500_000)

    _write_srt(
        working_dir / "origin_language_srt.srt",
        [
            "hello world",
            "released and every year thousands of old",
            "songs find new life",
            "thanks for watching",
        ],
    )
    _write_srt(
        working_dir / "target_language_srt.srt",
        [
            "你好，世界。",
            "released and every year thousands of old",
            "这些老歌重新焕发生命力。",
            "感谢观看。",
        ],
    )

    class _Task:
        target_lang = "zh_cn"
        enable_tts = False

    checker = QualityChecker()
    result = await checker.check(_Task(), working_dir)

    assert result["passed"] is False
    assert result["score"] < 100
    assert "存在英文残留" in result["details"]
    assert "英文残留行占比过高" in result["details"]


@pytest.mark.asyncio
async def test_quality_checker_flags_translation_meta_and_overlaps(tmp_path):
    working_dir = tmp_path / "task"
    working_dir.mkdir(parents=True, exist_ok=True)
    (working_dir / "output").mkdir(parents=True, exist_ok=True)
    (working_dir / "output" / "video.mp4").write_bytes(b"x" * 1_500_000)

    _write_custom_srt(
        working_dir / "origin_language_srt.srt",
        [
            ("00:00:00,000", "00:00:04,000", "this video is brought to you by incog go"),
            ("00:00:02,500", "00:00:07,000", "to the link in the description to get"),
        ],
    )
    _write_custom_srt(
        working_dir / "target_language_srt.srt",
        [
            ("00:00:00,000", "00:00:04,000", "以下是您要求翻译的英文文本：视频由 Incog 赞助。"),
            ("00:00:02,500", "00:00:07,000", "（注：翻译中保留了品牌名“Incog”。）"),
        ],
    )

    class _Task:
        target_lang = "zh_cn"
        enable_tts = False

    checker = QualityChecker()
    result = await checker.check(_Task(), working_dir)

    assert result["passed"] is False
    assert "存在模型注解/说明文字" in result["details"]
    assert "字幕时间轴存在重叠" in result["details"]
