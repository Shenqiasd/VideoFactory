"""
ASR 抽象层定义。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ASRResult:
    """ASR 结果。"""

    srt_content: str
    method: str
    source_lang: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseASRProvider(ABC):
    """ASR Provider 抽象接口。"""

    name: str = "unknown"

    @abstractmethod
    async def transcribe(
        self,
        *,
        video_url: str,
        video_path: Optional[str],
        source_lang: str,
    ) -> Optional[ASRResult]:
        """
        执行转写并返回 SRT 结果。

        Args:
            video_url: 视频 URL（可能为空）。
            video_path: 本地视频路径（可能为空）。
            source_lang: 源语言代码。

        Returns:
            Optional[ASRResult]: 成功返回结果，失败返回 None。
        """

