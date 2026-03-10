import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from translation.volcengine_ark import VolcengineArkTranslator  # noqa: E402


class _Config:
    def __init__(self, data):
        self._data = data

    def get(self, *keys, default=None):
        node = self._data
        for key in keys:
            if isinstance(node, dict):
                node = node.get(key)
            else:
                return default
            if node is None:
                return default
        return node


def test_volcengine_payload_uses_responses_shape():
    payload = VolcengineArkTranslator.build_translation_payload(
        model="doubao-seed-translation-250915",
        text="若夫淫雨霏霏，连月不开，阴风怒号，浊浪排空",
        source_lang="zh-CN",
        target_lang="en-US",
    )

    assert payload["model"] == "doubao-seed-translation-250915"
    content = payload["input"][0]["content"][0]
    assert content["type"] == "input_text"
    assert content["translation_options"]["source_language"] == "zh"
    assert content["translation_options"]["target_language"] == "en"


@pytest.mark.asyncio
async def test_volcengine_translate_text_parses_responses_output(monkeypatch):
    translator = VolcengineArkTranslator(
        config=_Config(
            {
                "translation": {
                    "volcengine_ark": {
                        "enabled": True,
                        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                        "api_key": "dummy",
                        "model": "doubao-seed-translation-250915",
                        "timeout": 60,
                    }
                }
            }
        )
    )

    class _Resp:
        def raise_for_status(self):
            return None

        @staticmethod
        def json():
            return {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Translated text",
                            }
                        ],
                    }
                ]
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            assert url.endswith("/responses")
            assert json["input"][0]["content"][0]["translation_options"]["source_language"] == "zh"
            assert json["input"][0]["content"][0]["translation_options"]["target_language"] == "en"
            return _Resp()

    monkeypatch.setattr("translation.volcengine_ark.httpx.AsyncClient", _Client)

    result = await translator.translate_text(
        text="中文",
        source_lang="zh-CN",
        target_lang="en-US",
    )

    assert result == "Translated text"
