"""
基于现有 llm 配置的翻译 Provider。
"""
from __future__ import annotations

from typing import Optional

import httpx

from core.config import Config

from .base import BaseTranslator, TranslatorRuntimeConfig


class LLMTranslator(BaseTranslator):
    """使用项目现有 llm 配置执行翻译。"""

    name = "llm"

    def __init__(self, config: Optional[Config] = None):
        cfg = config or Config()
        self._base_url = str(cfg.get("llm", "base_url", default="https://api.groq.com/openai/v1")).strip()
        self._api_key = str(cfg.get("llm", "api_key", default="")).strip()
        self._model = str(cfg.get("llm", "model", default="llama-3.3-70b-versatile")).strip()
        self._timeout = int(cfg.get("translation", "llm", "timeout", default=60))

    def runtime_config(self) -> TranslatorRuntimeConfig:
        return TranslatorRuntimeConfig(
            provider=self.name,
            base_url=self._base_url,
            api_key=self._api_key,
            model=self._model,
            timeout=self._timeout,
        )

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
                    "content": "你是专业翻译助手。仅输出翻译结果，不要额外解释。",
                },
                {
                    "role": "user",
                    "content": f"请把以下文本从{source_lang}翻译为{target_lang}：\n{text}",
                },
            ],
            "temperature": 0.2,
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
