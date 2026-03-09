"""
ASR 路由层。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from core.config import Config

from .base import ASRResult, BaseASRProvider
from .volcengine_asr import VolcengineASR
from .whisper_local import WhisperLocalASR
from .youtube_subtitle import YouTubeSubtitleASR

logger = logging.getLogger(__name__)

SUPPORTED_ASR_PROVIDERS = ("auto", "youtube", "volcengine", "whisper", "klicstudio")


class ASRRouter:
    """
    ASR 路由器（含降级策略）。

    规则：
    - provider = auto: 按 fallback_order 执行
    - provider = 指定值: 先执行指定 provider，若 allow_fallback=true 再执行 fallback_order
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        asr_cfg = self.config.get("asr", default={}) or {}

        provider = str(asr_cfg.get("provider", "auto")).strip().lower()
        self.provider = provider if provider in SUPPORTED_ASR_PROVIDERS else "auto"
        self.allow_fallback = bool(asr_cfg.get("allow_fallback", True))
        self.allow_klicstudio_fallback = bool(asr_cfg.get("allow_klicstudio_fallback", False))
        self.youtube_skip_download = bool(asr_cfg.get("youtube_skip_download", False))

        fallback_order = asr_cfg.get("fallback_order", ["youtube", "volcengine", "whisper"])
        if not isinstance(fallback_order, list):
            fallback_order = ["youtube", "volcengine", "whisper"]
        self.fallback_order: List[str] = []
        for method in fallback_order:
            m = str(method).strip().lower()
            if m in {"youtube", "volcengine", "whisper"} and m not in self.fallback_order:
                self.fallback_order.append(m)
        if not self.fallback_order:
            self.fallback_order = ["youtube", "volcengine", "whisper"]

        preferred_langs = asr_cfg.get("youtube_preferred_langs", [])
        if not isinstance(preferred_langs, list):
            preferred_langs = []

        self.providers: Dict[str, BaseASRProvider] = {
            "youtube": YouTubeSubtitleASR(preferred_langs=preferred_langs),
            "volcengine": VolcengineASR(config=self.config),
            "whisper": WhisperLocalASR(config=self.config),
        }

    def is_router_enabled(self) -> bool:
        """当前配置是否启用 ASR 路由分支。"""
        return self.provider in {"auto", "youtube", "volcengine", "whisper"}

    @staticmethod
    def can_use_youtube(video_url: str) -> bool:
        """给定 URL 是否属于 YouTube。"""
        return YouTubeSubtitleASR.is_youtube_url(video_url)

    def _resolve_method_chain(self, video_url: str) -> List[str]:
        if self.provider == "auto":
            methods = list(self.fallback_order)
        elif self.provider in {"youtube", "volcengine", "whisper"}:
            methods = [self.provider]
            if self.allow_fallback:
                methods.extend(m for m in self.fallback_order if m != self.provider)
        else:
            methods = []

        # 非 YouTube URL 不走 youtube provider
        if not YouTubeSubtitleASR.is_youtube_url(video_url):
            methods = [m for m in methods if m != "youtube"]

        dedup: List[str] = []
        for method in methods:
            if method not in dedup:
                dedup.append(method)
        return dedup

    async def transcribe(
        self,
        *,
        video_url: str,
        video_path: Optional[str],
        source_lang: str,
    ) -> ASRResult:
        """
        按路由链依次尝试转写，成功即返回。

        Raises:
            RuntimeError: 全部 provider 失败时抛出。
        """
        methods = self._resolve_method_chain(video_url=video_url)
        if not methods:
            raise RuntimeError("ASRRouter 未启用（provider 配置为旧模式或无效）")

        errors: List[str] = []
        for method in methods:
            provider = self.providers.get(method)
            if provider is None:
                continue

            try:
                result = await provider.transcribe(
                    video_url=video_url,
                    video_path=video_path,
                    source_lang=source_lang,
                )
            except Exception as exc:  # pragma: no cover - provider 未预期异常
                logger.warning("ASR provider %s 异常: %s", method, exc)
                errors.append(f"{method}: {exc}")
                continue

            if result and result.srt_content.strip():
                logger.info("✅ ASR 路由命中: %s", method)
                return result
            errors.append(f"{method}: empty")

        raise RuntimeError(f"ASR 全部降级失败: {'; '.join(errors)}")


__all__ = [
    "ASRResult",
    "ASRRouter",
    "SUPPORTED_ASR_PROVIDERS",
    "YouTubeSubtitleASR",
    "WhisperLocalASR",
    "VolcengineASR",
]
