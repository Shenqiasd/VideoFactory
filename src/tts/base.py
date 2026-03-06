"""
TTS 抽象层定义。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class TTSResult:
    """TTS 合成结果。"""

    audio_path: str
    provider: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseTTSProvider(ABC):
    """TTS Provider 抽象接口。"""

    name: str = "unknown"

    @abstractmethod
    async def synthesize(
        self,
        *,
        text: str,
        output_path: str,
        source_audio_path: Optional[str] = None,
        language: str = "",
        voice_type: Optional[str] = None,
    ) -> Optional[TTSResult]:
        """
        合成音频。

        Args:
            text: 要合成的文本。
            output_path: 目标音频路径。
            source_audio_path: 可选的参考音频（用于音色克隆）。
            language: 语言代码。
            voice_type: 可选音色 ID。

        Returns:
            Optional[TTSResult]: 成功返回结果，失败返回 None。
        """
