import json
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.task import Task  # noqa: E402
from production.global_translation_reviewer import GlobalTranslationReviewer  # noqa: E402
from production.sentence_regrouper import SentenceGroup  # noqa: E402


def _write_srt(path: Path, blocks: list[tuple[str, str, str]]):
    raw = []
    for i, (start, end, text) in enumerate(blocks, start=1):
        raw.append(f"{i}\n{start} --> {end}\n{text}")
    path.write_text("\n\n".join(raw) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_global_translation_reviewer_rewrites_music_terms_and_writes_report(tmp_path):
    working_dir = tmp_path / "vf_test_music_review"
    working_dir.mkdir(parents=True, exist_ok=True)

    _write_srt(
        working_dir / "origin_language_srt.srt",
        [
            (
                "00:00:00,000",
                "00:00:04,000",
                "the question is whether like a rolling stone is the greatest song ever made",
            ),
            (
                "00:00:04,000",
                "00:00:08,000",
                "and then bohemian rhapsody always comes up in the conversation",
            ),
        ],
    )
    _write_srt(
        working_dir / "target_language_srt.srt",
        [
            ("00:00:00,000", "00:00:04,000", "问题是《像滚石一样》是不是最伟大的歌。"),
            ("00:00:04,000", "00:00:08,000", "然后人们总会提到《波西米亚狂想曲》。"),
        ],
    )
    _write_srt(
        working_dir / "bilingual_srt.srt",
        [
            (
                "00:00:00,000",
                "00:00:04,000",
                "问题是《像滚石一样》是不是最伟大的歌。\nthe question is whether like a rolling stone is the greatest song ever made",
            ),
            (
                "00:00:04,000",
                "00:00:08,000",
                "然后人们总会提到《波西米亚狂想曲》。\nand then bohemian rhapsody always comes up in the conversation",
            ),
        ],
    )
    (working_dir / "target_language.txt").write_text(
        "问题是《像滚石一样》是不是最伟大的歌。\n然后人们总会提到《波西米亚狂想曲》。\n",
        encoding="utf-8",
    )

    task = Task(
        source_url="https://example.com/video",
        source_title="The Greatest Song Ever Made?",
        source_lang="en",
        target_lang="zh_cn",
    )
    task.translated_title = "最伟大的歌曲是什么？"
    task.translated_description = "视频讨论《像滚石一样》和《波西米亚狂想曲》。"

    groups = [
        SentenceGroup(
            cue_indexes=[0],
            source_lines=["the question is whether like a rolling stone is the greatest song ever made"],
            source_text="the question is whether like a rolling stone is the greatest song ever made",
        ),
        SentenceGroup(
            cue_indexes=[1],
            source_lines=["and then bohemian rhapsody always comes up in the conversation"],
            source_text="and then bohemian rhapsody always comes up in the conversation",
        ),
    ]

    reviewer = GlobalTranslationReviewer()
    reviewer.api_base = "http://127.0.0.1:9/v1"
    reviewer.api_key = "test-key"
    reviewer.model = "test-model"

    async def _fake_detect_domain_and_glossary(*, task, source_groups):
        del task, source_groups
        return {
            "domain": "music",
            "confidence": 0.98,
            "reason": "内容在讨论歌曲排名。",
            "glossary": [
                {"term": "Like a Rolling Stone", "category": "song"},
                {"term": "Bohemian Rhapsody", "category": "song"},
            ],
        }

    async def _fake_rewrite_group_chunk(*, task, chunk_sources, chunk_targets, glossary):
        del task, chunk_sources, chunk_targets, glossary
        return [
            "问题是 Like a Rolling Stone 到底是不是最伟大的歌曲。",
            "然后人们总会提到 Bohemian Rhapsody。",
        ]

    async def _fake_rewrite_metadata(*, task, glossary, translated_title, translated_description):
        del task, glossary, translated_title, translated_description
        return {
            "title": "Like a Rolling Stone 与 Bohemian Rhapsody 谁更伟大？",
            "description": "这支视频讨论 Like a Rolling Stone 和 Bohemian Rhapsody。",
        }

    reviewer._detect_domain_and_glossary = _fake_detect_domain_and_glossary  # type: ignore[method-assign]
    reviewer._rewrite_group_chunk = _fake_rewrite_group_chunk  # type: ignore[method-assign]
    reviewer._rewrite_metadata = _fake_rewrite_metadata  # type: ignore[method-assign]

    result = await reviewer.review(
        task,
        working_dir,
        groups=groups,
        origin_text="the question is whether like a rolling stone is the greatest song ever made\nand then bohemian rhapsody always comes up in the conversation",
        target_text="问题是《像滚石一样》是不是最伟大的歌。\n然后人们总会提到《波西米亚狂想曲》。",
    )

    assert result.passed is True
    assert result.fixed is True
    assert result.domain == "music"
    assert "Like a Rolling Stone" in result.translated_title
    assert "Bohemian Rhapsody" in result.translated_description
    assert result.report["status"] == "passed"
    assert result.report["issues_before"]
    assert result.report["issues_after"] == []

    target_srt = (working_dir / "target_language_srt.srt").read_text(encoding="utf-8")
    assert "Like a Rolling Stone" in target_srt
    assert "Bohemian Rhapsody" in target_srt

    report_path = working_dir / "global_review_report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["domain"]["name"] == "music"


@pytest.mark.asyncio
async def test_global_translation_reviewer_skips_when_llm_key_is_placeholder(tmp_path):
    working_dir = tmp_path / "vf_test_global_review_placeholder"
    working_dir.mkdir(parents=True, exist_ok=True)

    _write_srt(
        working_dir / "origin_language_srt.srt",
        [("00:00:00,000", "00:00:02,000", "hello world")],
    )
    _write_srt(
        working_dir / "target_language_srt.srt",
        [("00:00:00,000", "00:00:02,000", "你好，世界")],
    )

    task = Task(
        source_url="https://example.com/video",
        source_title="Demo",
        source_lang="en",
        target_lang="zh_cn",
    )

    reviewer = GlobalTranslationReviewer()
    reviewer.api_base = "https://api.example.com/v1"
    reviewer.api_key = "YOUR_API_KEY_HERE"
    reviewer.model = "demo-model"

    result = await reviewer.review(
        task,
        working_dir,
        groups=[],
        origin_text="hello world",
        target_text="你好，世界",
    )

    assert reviewer.fail_open is True
    assert result.passed is True
    assert result.skipped is True
    assert result.report["status"] == "skipped"
    assert "api_key" in result.message
