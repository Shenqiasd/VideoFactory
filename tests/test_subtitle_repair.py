import json
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from production.subtitle_repair import SubtitleRepairer  # noqa: E402


def _write_srt(path: Path, lines: list[str]):
    blocks = []
    for i, line in enumerate(lines, start=1):
        blocks.append(
            f"{i}\n00:00:0{i},000 --> 00:00:0{i},800\n{line}"
        )
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_subtitle_repair_fixes_untranslated_lines(monkeypatch, tmp_path):
    origin = tmp_path / "origin_language_srt.srt"
    target = tmp_path / "target_language_srt.srt"
    bilingual = tmp_path / "bilingual_srt.srt"

    _write_srt(origin, ["Hello world", "How are you"])
    _write_srt(target, ["Hello world", "How are you"])
    _write_srt(bilingual, ["Hello world\nHello world", "How are you\nHow are you"])

    class _Task:
        target_lang = "zh_cn"
        source_lang = "en"

    repairer = SubtitleRepairer()

    async def _fake_translate(texts, target_lang, source_lang="auto"):
        return ["你好，世界", "你好吗"]

    monkeypatch.setattr(repairer, "_translate_batch", _fake_translate)
    result = await repairer.repair_if_needed(_Task(), tmp_path)

    assert result.passed is True
    assert result.repaired is True
    assert result.repaired_lines >= 2

    target_content = target.read_text(encoding="utf-8")
    bilingual_content = bilingual.read_text(encoding="utf-8")
    assert "你好，世界" in target_content
    assert "你好吗" in target_content
    assert "你好，世界" in bilingual_content
    assert "Hello world" in bilingual_content


@pytest.mark.asyncio
async def test_subtitle_repair_fails_when_quality_still_low(monkeypatch, tmp_path):
    origin = tmp_path / "origin_language_srt.srt"
    target = tmp_path / "target_language_srt.srt"
    bilingual = tmp_path / "bilingual_srt.srt"

    _write_srt(origin, ["Hello world", "How are you"])
    _write_srt(target, ["Hello world", "How are you"])
    _write_srt(bilingual, ["Hello world\nHello world", "How are you\nHow are you"])

    class _Task:
        target_lang = "zh_cn"
        source_lang = "en"

    repairer = SubtitleRepairer()
    repairer.min_zh_line_ratio = 0.95
    repairer.max_unchanged_ratio = 0.05
    repairer.translation_provider = "llm"

    async def _fake_translate(texts, target_lang, source_lang="auto"):
        return ["Hello world", "How are you"]

    monkeypatch.setattr(repairer, "_translate_batch", _fake_translate)
    result = await repairer.repair_if_needed(_Task(), tmp_path)

    assert result.passed is False
    assert "字幕未达标" in result.message


@pytest.mark.asyncio
async def test_subtitle_repair_uses_context_for_english_residue(monkeypatch, tmp_path):
    origin = tmp_path / "origin_language_srt.srt"
    target = tmp_path / "target_language_srt.srt"
    bilingual = tmp_path / "bilingual_srt.srt"

    _write_srt(
        origin,
        [
            "every year thousands of new songs are",
            "released and every year thousands of old",
            "songs find new life in an everchanging",
        ],
    )
    _write_srt(
        target,
        [
            "每年都有成千上万首新歌问世。",
            "released and every year thousands of old",
            "songs find new life in an everchanging",
        ],
    )
    _write_srt(
        bilingual,
        [
            "每年都有成千上万首新歌问世。\nevery year thousands of new songs are",
            "released and every year thousands of old\nreleased and every year thousands of old",
            "songs find new life in an everchanging\nsongs find new life in an everchanging",
        ],
    )

    class _Task:
        target_lang = "zh_cn"
        source_lang = "en"

    repairer = SubtitleRepairer()
    repairer.translation_provider = "volcengine_ark"
    repairer.api_base = "https://ark.cn-beijing.volces.com/api/v3"
    repairer.model = "doubao-seed-translation-250915"
    repairer.api_key = "dummy"

    async def _fake_translate(texts, target_lang, source_lang="auto"):
        del target_lang, source_lang
        return list(texts)

    async def _fake_context_translate(*, index, lines, source_lang, target_lang):
        del lines, source_lang, target_lang
        mapping = {
            1: "每年也有成千上万首老歌焕发新生。",
            2: "在不断变化的文化环境中重新获得生命力。",
        }
        return mapping.get(index, "")

    monkeypatch.setattr(repairer, "_translate_batch", _fake_translate)
    monkeypatch.setattr(repairer, "_translate_line_with_context", _fake_context_translate)
    result = await repairer.repair_if_needed(_Task(), tmp_path)

    assert result.passed is True
    target_content = target.read_text(encoding="utf-8")
    assert "每年也有成千上万首老歌焕发新生。" in target_content
    assert "在不断变化的文化环境中重新获得生命力。" in target_content


def test_extract_context_line_translation_prefers_current_line():
    raw = (
        "[上一句] 每年都有成千上万首新歌发布，\n"
        "[当前句] 每年也有成千上万首老歌在不断变化的音乐环境中焕发新生。\n"
        "[下一句] 音乐文化不断变化。\n"
    )
    assert (
        SubtitleRepairer._extract_context_line_translation(raw)
        == "每年也有成千上万首老歌在不断变化的音乐环境中焕发新生。"
    )


def test_sanitize_translation_text_strips_meta_artifacts():
    raw = (
        "以下是您要求翻译的英文文本： **视频由 Incog 赞助。** "
        "（注：翻译中保留了品牌名“Incog”，并根据中文表达习惯调整了句式结构。）"
    )

    assert SubtitleRepairer.sanitize_translation_text(raw) == "视频由 Incog 赞助。"


@pytest.mark.asyncio
async def test_subtitle_repair_repairs_translation_meta_artifacts(monkeypatch, tmp_path):
    origin = tmp_path / "origin_language_srt.srt"
    target = tmp_path / "target_language_srt.srt"
    bilingual = tmp_path / "bilingual_srt.srt"

    _write_srt(
        origin,
        [
            "this video is brought to you by incog go",
            "to the link in the description to get",
            "60% off an annual plan there's been a",
            "question lingering in the back of my",
        ],
    )
    _write_srt(
        target,
        [
            "以下是您要求翻译的英文文本： **视频由 Incog 赞助。**",
            "（注：翻译中保留了品牌名“Incog”，",
            "并根据中文表达习惯调整了句式结构，",
            "译为“一直有个问题在我脑海里挥之不去”。）",
        ],
    )
    _write_srt(
        bilingual,
        [
            "以下是您要求翻译的英文文本： **视频由 Incog 赞助。**\nthis video is brought to you by incog go",
            "（注：翻译中保留了品牌名“Incog”，\nto the link in the description to get",
            "并根据中文表达习惯调整了句式结构，\n60% off an annual plan there's been a",
            "译为“一直有个问题在我脑海里挥之不去”。）\nquestion lingering in the back of my",
        ],
    )

    class _Task:
        target_lang = "zh_cn"
        source_lang = "en"

    repairer = SubtitleRepairer()

    async def _fake_translate(texts, target_lang, source_lang="auto"):
        del target_lang, source_lang
        return [
            "视频由 Incog 赞助。",
            "点击描述中的链接。",
            "即可享受年度计划六折优惠。",
            "一个问题一直萦绕在我的脑海中。",
        ]

    monkeypatch.setattr(repairer, "_translate_batch", _fake_translate)
    result = await repairer.repair_if_needed(_Task(), tmp_path)

    assert result.passed is True
    assert "以下是您要求翻译的英文文本" not in target.read_text(encoding="utf-8")
    assert "注：" not in target.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_translate_batch_keeps_partial_results(monkeypatch):
    repairer = SubtitleRepairer()
    repairer.max_retries = 0
    repairer.translation_provider = "llm"
    repairer.api_base = "https://api.example.com/v1"
    repairer.model = "test-model"

    class _Resp:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "choices": [
                    {
                        "message": {
                            "content": '["你好"]',
                        }
                    }
                ]
            }

    class _Client:
        async def post(self, *args, **kwargs):
            return _Resp()

    async def _fake_get_client():
        return _Client()

    monkeypatch.setattr(repairer, "_get_client", _fake_get_client)
    repairer.api_key = "dummy"

    translated = await repairer._translate_batch(["hello", "world"], "zh_cn")
    assert translated == ["你好", "world"]


@pytest.mark.asyncio
async def test_translate_batch_splits_when_parse_fails(monkeypatch):
    repairer = SubtitleRepairer()
    repairer.max_retries = 0
    repairer.translation_provider = "llm"
    repairer.api_base = "https://api.example.com/v1"
    repairer.model = "test-model"

    class _Resp:
        def __init__(self, content: str):
            self.status_code = 200
            self._content = content
            self.text = ""

        def json(self):
            return {"choices": [{"message": {"content": self._content}}]}

    class _Client:
        async def post(self, *args, **kwargs):
            payload = kwargs.get("json", {})
            user_content = payload.get("messages", [{}, {}])[1].get("content", "")
            marker = "输入: "
            if marker in user_content:
                arr_raw = user_content.split(marker, 1)[1].strip()
                values = json.loads(arr_raw)
            else:
                values = []

            if len(values) > 1:
                return _Resp("invalid json")
            if values and values[0] == "hello":
                return _Resp('["你好"]')
            if values and values[0] == "world":
                return _Resp('["世界"]')
            return _Resp("[]")

    async def _fake_get_client():
        return _Client()

    monkeypatch.setattr(repairer, "_get_client", _fake_get_client)
    repairer.api_key = "dummy"

    translated = await repairer._translate_batch(["hello", "world"], "zh_cn")
    assert translated == ["你好", "世界"]


@pytest.mark.asyncio
async def test_translate_batch_uses_volcengine_responses_api(monkeypatch):
    repairer = SubtitleRepairer()
    repairer.max_retries = 0
    repairer.translation_provider = "volcengine_ark"
    repairer.translation_enabled = True
    repairer.api_base = "https://ark.cn-beijing.volces.com/api/v3"
    repairer.model = "doubao-seed-translation-250915"
    repairer.api_key = "dummy"

    class _Resp:
        def __init__(self, text: str):
            self.status_code = 200
            self._text = text
            self.text = text

        def json(self):
            return {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": self._text,
                            }
                        ],
                    }
                ]
            }

    class _Client:
        async def post(self, *args, **kwargs):
            payload = kwargs.get("json", {})
            message = payload.get("input", [{}])[0]
            content = (message.get("content") or [{}])[0]
            assert content.get("translation_options", {}).get("source_language") == "en"
            assert content.get("translation_options", {}).get("target_language") == "zh"
            text = content.get("text", "")
            return _Resp(f"{text}-zh")

    async def _fake_get_client():
        return _Client()

    monkeypatch.setattr(repairer, "_get_client", _fake_get_client)

    translated = await repairer._translate_batch(["hello", "world"], "zh_cn", source_lang="en")
    assert translated == ["hello-zh", "world-zh"]


@pytest.mark.asyncio
async def test_translate_batch_raises_for_local_llm_runtime_failures(monkeypatch):
    repairer = SubtitleRepairer()
    repairer.max_retries = 0
    repairer.translation_provider = "local_llm"
    repairer.translation_enabled = True
    repairer.api_base = "http://127.0.0.1:1234/v1"
    repairer.model = "qwen2.5-7b-instruct"
    repairer.api_key = ""

    class _Client:
        async def post(self, *args, **kwargs):
            raise RuntimeError("connection refused")

    async def _fake_get_client():
        return _Client()

    monkeypatch.setattr(repairer, "_get_client", _fake_get_client)

    with pytest.raises(RuntimeError, match="本地翻译模型调用失败"):
        await repairer._translate_batch(["hello"], "zh_cn")
