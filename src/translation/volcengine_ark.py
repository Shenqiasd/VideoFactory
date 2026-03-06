"""
火山方舟翻译 Provider（OpenAI 兼容）。
"""
from __future__ import annotations

from typing import Optional

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
        self._model = str(volc_cfg.get("model", "doubao-seed-translation")).strip()
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

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是专业翻译助手。只返回译文，不要解释。",
                },
                {
                    "role": "user",
                    "content": f"Translate from {source_lang} to {target_lang}:\n{text}",
                },
            ],
            "temperature": 0.0,
            "max_tokens": 1000,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            return "\n".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict)
            ).strip()
        return str(content).strip()
