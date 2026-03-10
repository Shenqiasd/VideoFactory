"""
火山方舟翻译 Provider（OpenAI 兼容）。
"""
from __future__ import annotations

from typing import Optional, Any

import httpx

from core.config import Config

from .base import BaseTranslator, TranslatorRuntimeConfig


class VolcengineArkTranslator(BaseTranslator):
    """火山方舟翻译实现。"""

    name = "volcengine_ark"

    def __init__(self, config: Optional[Config] = None):
        cfg = config or Config()
        volc_cfg = cfg.get("translation", "volcengine_ark", default={}) or {}

        self.enabled = bool(volc_cfg.get("enabled", False))
        self._base_url = str(
            volc_cfg.get("base_url", "https://ark.cn-beijing.volces.com/api/v3")
        ).strip()
        self._api_key = str(volc_cfg.get("api_key", "")).strip()
        self._model = str(volc_cfg.get("model", "doubao-seed-translation-250915")).strip()
        self._timeout = int(volc_cfg.get("timeout", 60))

    def runtime_config(self) -> TranslatorRuntimeConfig:
        return TranslatorRuntimeConfig(
            provider=self.name,
            base_url=self._base_url,
            api_key=self._api_key,
            model=self._model,
            timeout=self._timeout,
        )

    def is_configured(self) -> bool:
        return self.enabled and bool(self._api_key) and bool(self._model)

    @staticmethod
    def normalize_translation_language(lang: str) -> str:
        normalized = str(lang or "").strip().replace("_", "-").lower()
        if not normalized:
            return ""
        if normalized.startswith("zh"):
            return "zh"
        if normalized.startswith("en"):
            return "en"
        return normalized.split("-", 1)[0]

    @classmethod
    def build_translation_payload(
        cls,
        *,
        model: str,
        text: str,
        source_lang: str,
        target_lang: str,
    ) -> dict[str, Any]:
        translation_options = {
            "target_language": cls.normalize_translation_language(target_lang),
        }
        normalized_source = cls.normalize_translation_language(source_lang)
        if normalized_source and normalized_source != "auto":
            translation_options["source_language"] = normalized_source

        return {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": text,
                            "translation_options": translation_options,
                        }
                    ],
                }
            ],
        }

    @staticmethod
    def extract_output_text(data: dict[str, Any]) -> str:
        if not isinstance(data, dict):
            return ""

        direct = data.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()

        chunks = []
        for item in data.get("output", []) if isinstance(data.get("output"), list) else []:
            if not isinstance(item, dict):
                continue
            contents = item.get("content", [])
            if not isinstance(contents, list):
                continue
            for content in contents:
                if not isinstance(content, dict):
                    continue
                if content.get("type") == "output_text":
                    text = str(content.get("text", "")).strip()
                    if text:
                        chunks.append(text)
        return "\n".join(chunks).strip()

    async def translate_text(
        self,
        *,
        text: str,
        source_lang: str = "en",
        target_lang: str = "zh-CN",
    ) -> str:
        if not text.strip():
            return ""
        if not self._api_key:
            return ""

        payload = self.build_translation_payload(
            model=self._model,
            text=text,
            source_lang=source_lang,
            target_lang=target_lang,
        )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url.rstrip('/')}/responses",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        resp.raise_for_status()
        data = resp.json()
        return self.extract_output_text(data)
