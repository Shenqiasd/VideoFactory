"""
翻译 Provider 抽象定义。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TranslatorRuntimeConfig:
    """翻译 Provider 运行时配置。"""

    provider: str
    base_url: str
    api_key: str
    model: str
    timeout: int = 60


class BaseTranslator(ABC):
    """翻译 Provider 抽象接口。"""

    name: str = "unknown"

    @abstractmethod
    def runtime_config(self) -> TranslatorRuntimeConfig:
        """
        返回 OpenAI 兼容接口运行时配置。
        """

    @abstractmethod
    async def translate_text(
        self,
        *,
        text: str,
        source_lang: str = "en",
        target_lang: str = "zh-CN",
    ) -> str:
        """
        翻译单段文本。
        """
