import sys
from pathlib import Path

import yaml


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.config import Config  # noqa: E402
from translation import get_translator  # noqa: E402
from translation.local_llm import LocalLLMTranslator  # noqa: E402


def test_get_translator_keeps_local_llm_without_fallback(monkeypatch, tmp_path):
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "llm": {
                    "base_url": "https://api.groq.com/openai/v1",
                    "api_key": "groq-key",
                    "model": "llama-3.3-70b-versatile",
                },
                "translation": {
                    "provider": "local_llm",
                    "local_llm": {
                        "enabled": False,
                        "base_url": "http://127.0.0.1:1234/v1",
                        "api_key": "",
                        "model": "",
                        "timeout": 120,
                    },
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("VF_CONFIG", str(config_path))
    Config.reset()

    translator = get_translator()

    assert isinstance(translator, LocalLLMTranslator)
    assert translator.runtime_config().provider == "local_llm"
    assert translator.is_configured() is False

    Config.reset()
