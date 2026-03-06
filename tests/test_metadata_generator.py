import asyncio
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from factory.metadata import MetadataGenerator  # noqa: E402


class MockMetadataGenerator(MetadataGenerator):
    def __init__(self, responses):
        super().__init__(strict_json=False, max_retries=2)
        self._responses = list(responses)

    async def _call_llm(self, prompt: str, max_tokens: int = 2000):
        if not self._responses:
            return None
        return self._responses.pop(0)


def test_parse_extract_and_repair_layers():
    gen = MetadataGenerator(strict_json=False)

    obj, mode, err = gen._parse_with_layers('{"title":"a","description":"b","tags":["x"]}')
    assert err == ""
    assert mode == "strict"
    assert obj["title"] == "a"

    obj, mode, err = gen._parse_with_layers('输出如下:\n```json\n{"title":"a","description":"b","tags":["x"]}\n```')
    assert err == ""
    assert mode in ("strict", "extract")
    assert obj["description"] == "b"

    obj, mode, err = gen._parse_with_layers('{"title":"a","description":"b","tags":["x",],}')
    assert err == ""
    assert mode == "repair"
    assert obj["tags"] == ["x"]


def test_generate_for_platform_retry_and_success():
    responses = [
        "```json\n{\"title\": \"bad\", \"description\": \"x\", \"tags\": \"not_list\"}\n```",
        '{"title":"ok title","description":"ok desc","tags":["a","b"]}',
    ]
    gen = MockMetadataGenerator(responses)

    result = asyncio.run(
        gen.generate_for_platform(
            platform="bilibili",
            original_title="origin",
            translated_title="translated",
            transcript="demo transcript",
        )
    )

    assert result["title"] == "ok title"
    assert result["parse_mode"] in ("strict", "extract")
    assert result["retry_count"] == 1

