"""
本地 OpenAI 兼容翻译 Provider。
"""
from __future__ import annotations

from typing import Optional

import httpx

from core.config import Config

from .base import BaseTranslator, TranslatorRuntimeConfig


class LocalLLMTranslator(BaseTranslator):
    """使用本地 OpenAI 兼容模型执行翻译。"""

    name = "local_llm"

    def __init__(self, config: Optional[Config] = None):
        cfg = config or Config()
        local_cfg = cfg.get("translation", "local_llm", default={}) or {}

        self.enabled = bool(local_cfg.get("enabled", False))
        self._base_url = str(local_cfg.get("base_url", "http://127.0.0.1:1234/v1")).strip()
        self._api_key = str(local_cfg.get("api_key", "")).strip()
        self._model = str(local_cfg.get("model", "")).strip()
        self._timeout = int(local_cfg.get("timeout", 120))

    def runtime_config(self) -> TranslatorRuntimeConfig:
        return TranslatorRuntimeConfig(
            provider=self.name,
            base_url=self._base_url,
            api_key=self._api_key,
            model=self._model,
            timeout=self._timeout,
        )

    def is_configured(self) -> bool:
        return self.enabled and bool(self._base_url) and bool(self._model)

    async def translate_text(
        self,
        *,
        text: str,
        source_lang: str = "en",
        target_lang: str = "zh-CN",
    ) -> str:
        if not text.strip():
            return ""
        if not self.is_configured():
            raise RuntimeError("本地翻译模型未配置完整，请检查 translation.local_llm 配置")

        headers = {
            "Content-Type": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

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
                headers=headers,
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
