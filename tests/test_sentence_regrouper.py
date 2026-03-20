import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from production.sentence_regrouper import SentenceRegrouper  # noqa: E402


def _entry(text: str, *, start: str, end: str):
    return {
        "start": start,
        "end": end,
        "lines": [text],
    }


def test_group_entries_merges_fragmented_cues():
    regrouper = SentenceRegrouper()
    entries = [
        _entry("question lingering in the back of my", start="00:00:07,279", end="00:00:11,240"),
        _entry("head for the past few months it's not", start="00:00:09,000", end="00:00:13,400"),
        _entry("exactly a novel question in fact it's", start="00:00:11,240", end="00:00:15,519"),
        _entry("one that I've asked myself on and off", start="00:00:13,400", end="00:00:17,960"),
        _entry("for years it's one that a lot of music", start="00:00:15,519", end="00:00:19,760"),
    ]

    groups = regrouper.group_entries(entries)

    assert len(groups) == 2
    assert groups[0].cue_indexes == [0, 1, 2, 3]
    assert groups[1].cue_indexes == [4]
    assert "question lingering in the back of my" in groups[0].source_text
    assert "one that I've asked myself on and off" in groups[0].source_text


def test_project_translation_splits_back_to_original_cues():
    regrouper = SentenceRegrouper()
    source_lines = [
        "question lingering in the back of my",
        "head for the past few months it's not",
        "exactly a novel question in fact it's",
        "one that I've asked myself on and off",
    ]
    translated = "过去几个月来，一个问题一直萦绕在我的脑海里。虽然这并不算新奇，但事实上，我时不时就会问自己这个问题。"

    projected = regrouper.project_translation(translated, source_lines)

    assert len(projected) == 4
    assert all(part.strip() for part in projected)
    assert projected[0].startswith("过去几个月来")
    assert projected[-1].endswith("问题。")
    assert all(len(part.strip()) >= 4 for part in projected)


def test_project_translation_keeps_book_title_intact():
    regrouper = SentenceRegrouper()
    source_lines = [
        "SG to perform an epic piece of music",
        "called Stairway to",
        "Heaven",
    ]
    translated = "献上了一首气势磅礴的乐曲，名为《天堂阶梯》。"

    projected = regrouper.project_translation(translated, source_lines)

    assert len(projected) == 3
    assert all("《天堂阶" not in part for part in projected[:-1])
    assert projected[-1].endswith("《天堂阶梯》。")


def test_project_translation_keeps_parenthetical_phrase_together():
    regrouper = SentenceRegrouper()
    source_lines = [
        "this recording was captured",
        "(live version)",
        "during the tour",
    ]
    translated = "这段录音是在巡演期间录制的（现场版）。"

    projected = regrouper.project_translation(translated, source_lines)

    assert len(projected) == 3
    assert all(part.strip() for part in projected)
    assert projected[-1].endswith("（现场版）。")
    assert not any(part in {"（", "现场版）", "）"} for part in projected)


def test_project_translation_does_not_leave_function_word_tail():
    regrouper = SentenceRegrouper()
    source_lines = [
        "this song is a tribute",
        "to",
        "rock and roll",
    ]
    translated = "这首歌是对摇滚乐的致敬。"

    projected = regrouper.project_translation(translated, source_lines)

    assert len(projected) == 3
    assert all(part.strip() for part in projected)
    assert "对" not in {part.strip() for part in projected}
    assert projected[-1].endswith("致敬。")


@pytest.mark.asyncio
async def test_translate_entries_preserves_cue_count():
    regrouper = SentenceRegrouper()
    entries = [
        _entry("every year thousands of new songs are", start="00:00:53,399", end="00:00:57,520"),
        _entry("released and every year thousands of old", start="00:00:55,280", end="00:01:00,000"),
        _entry("songs find New Life in an everchanging", start="00:00:57,520", end="00:01:02,039"),
    ]

    async def _fake_translate(texts, target_lang, source_lang):
        del target_lang, source_lang
        assert len(texts) == 1
        return ["每年，成千上万的新歌问世；每年，成千上万的旧歌也在不断变化的文化环境中焕发新生机。"]

    result = await regrouper.translate_entries(
        entries,
        target_lang="zh_cn",
        source_lang="en",
        translate_lines=_fake_translate,
    )

    assert len(result.cue_lines) == len(entries)
    assert all(line.strip() for line in result.cue_lines)
    assert "旧歌" in "".join(result.cue_lines)
