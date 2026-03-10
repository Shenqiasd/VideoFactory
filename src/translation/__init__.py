"""
翻译 Provider 路由。
"""
from __future__ import annotations

from typing import Optional

from core.config import Config

from .base import BaseTranslator
from .llm_translator import LLMTranslator
from .local_llm import LocalLLMTranslator
from .volcengine_ark import VolcengineArkTranslator

SUPPORTED_TRANSLATION_PROVIDERS = ("llm", "local_llm", "volcengine_ark")


def get_translator(config: Optional[Config] = None, provider: Optional[str] = None) -> BaseTranslator:
    """
    根据配置返回翻译 Provider，失败时自动回退到 llm。
    """
    cfg = config or Config()
    selected = (provider or cfg.get("translation", "provider", default="llm") or "llm").strip().lower()

    if selected == "local_llm":
        return LocalLLMTranslator(config=cfg)

    if selected == "volcengine_ark":
        volc = VolcengineArkTranslator(config=cfg)
        if volc.is_configured():
            return volc

    return LLMTranslator(config=cfg)


__all__ = [
    "BaseTranslator",
    "LLMTranslator",
    "LocalLLMTranslator",
    "VolcengineArkTranslator",
    "SUPPORTED_TRANSLATION_PROVIDERS",
    "get_translator",
]
