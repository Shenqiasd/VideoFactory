"""
YouTube 字幕抓取 ASR Provider。
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import parse_qs, urlparse

from .base import ASRResult, BaseASRProvider

logger = logging.getLogger(__name__)


def _format_srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


class YouTubeSubtitleASR(BaseASRProvider):
    """直接获取 YouTube 字幕并转换为 SRT。"""

    name = "youtube"
    _VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")

    def __init__(self, preferred_langs: Optional[Sequence[str]] = None):
        self.preferred_langs = [lang.strip() for lang in (preferred_langs or []) if str(lang).strip()]

    @classmethod
    def extract_video_id(cls, video_url: str) -> Optional[str]:
        """从 YouTube URL 中提取 video_id。"""
        if not video_url:
            return None
        parsed = urlparse(video_url)
        host = (parsed.netloc or "").lower()
        path = parsed.path or ""

        if "youtu.be" in host:
            candidate = path.strip("/").split("/")[0]
            return candidate if cls._VIDEO_ID_RE.match(candidate) else None

        if "youtube.com" in host:
            qs = parse_qs(parsed.query or "")
            if "v" in qs and qs["v"]:
                candidate = qs["v"][0]
                return candidate if cls._VIDEO_ID_RE.match(candidate) else None

            parts = [p for p in path.split("/") if p]
            if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
                candidate = parts[1]
                return candidate if cls._VIDEO_ID_RE.match(candidate) else None

        return None

    @staticmethod
    def is_youtube_url(video_url: str) -> bool:
        parsed = urlparse(video_url or "")
        host = (parsed.netloc or "").lower()
        return "youtube.com" in host or "youtu.be" in host

    def _build_lang_candidates(self, source_lang: str) -> List[str]:
        raw = [source_lang, source_lang.replace("_", "-"), source_lang.split("-")[0]]
        if source_lang.startswith("en"):
            raw.extend(["en", "en-US", "en-GB"])
        if source_lang.startswith("zh"):
            raw.extend(["zh", "zh-CN", "zh-Hans"])
        raw.extend(self.preferred_langs)

        dedup: List[str] = []
        for lang in raw:
            l = (lang or "").strip()
            if l and l not in dedup:
                dedup.append(l)
        return dedup

    @staticmethod
    def _to_srt(items: Sequence[Dict[str, Any]]) -> str:
        blocks: List[str] = []
        index = 1
        for item in items:
            text = html.unescape(str(item.get("text", ""))).replace("\n", " ").strip()
            if not text:
                continue
            start = float(item.get("start", 0.0) or 0.0)
            duration = float(item.get("duration", 0.0) or 0.0)
            end = start + (duration if duration > 0.2 else 1.2)
            blocks.append(
                f"{index}\n{_format_srt_time(start)} --> {_format_srt_time(end)}\n{text}\n"
            )
            index += 1
        return "\n".join(blocks).strip() + ("\n" if blocks else "")

    def _fetch_transcript_items(self, video_id: str, source_lang: str) -> List[Dict[str, Any]]:
        """
        同步调用 youtube-transcript-api，供 asyncio.to_thread 使用。
        """
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except Exception as exc:  # pragma: no cover - 依赖未安装环境
            raise RuntimeError(
                "youtube-transcript-api 未安装，请安装后启用 YouTube 字幕抓取"
            ) from exc

        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        lang_candidates = self._build_lang_candidates(source_lang)

        # 优先手动字幕
        for lang in lang_candidates:
            try:
                transcript = transcript_list.find_manually_created_transcript([lang])
                return list(transcript.fetch())
            except Exception:
                continue

        # 降级自动字幕
        for lang in lang_candidates:
            try:
                transcript = transcript_list.find_generated_transcript([lang])
                return list(transcript.fetch())
            except Exception:
                continue

        # 最后兜底：取第一个可用字幕
        for transcript in transcript_list:
            try:
                return list(transcript.fetch())
            except Exception:
                continue

        return []

    async def transcribe(
        self,
        *,
        video_url: str,
        video_path: Optional[str],
        source_lang: str,
    ) -> Optional[ASRResult]:
        del video_path  # 不使用本地路径
        video_id = self.extract_video_id(video_url)
        if not video_id:
            return None

        try:
            items = await asyncio.to_thread(self._fetch_transcript_items, video_id, source_lang)
        except Exception as exc:
            logger.warning("YouTube 字幕抓取失败: %s", exc)
            return None

        if not items:
            logger.info("YouTube 字幕为空，video_id=%s", video_id)
            return None

        srt_content = self._to_srt(items)
        if not srt_content.strip():
            return None

        logger.info("✅ YouTube 字幕抓取成功: video_id=%s, lines=%s", video_id, len(items))
        return ASRResult(
            srt_content=srt_content,
            method=self.name,
            source_lang=source_lang,
            metadata={"video_id": video_id, "line_count": len(items)},
        )

