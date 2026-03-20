"""
YouTube 字幕抓取 ASR Provider。
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import tempfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import parse_qs, urlparse
from pathlib import Path

from core.config import Config
from source.ytdlp_runtime import build_ytdlp_base_cmd

from .base import ASRResult, BaseASRProvider

logger = logging.getLogger(__name__)


def _format_srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _parse_caption_timestamp(raw: str) -> float:
    text = str(raw or "").strip().split()[0].replace(",", ".")
    parts = text.split(":")
    if len(parts) == 2:
        minutes = int(parts[0] or 0)
        seconds = float(parts[1] or 0.0)
        return minutes * 60 + seconds
    if len(parts) == 3:
        hours = int(parts[0] or 0)
        minutes = int(parts[1] or 0)
        seconds = float(parts[2] or 0.0)
        return hours * 3600 + minutes * 60 + seconds
    raise ValueError(f"unsupported caption timestamp: {raw}")


class YouTubeSubtitleASR(BaseASRProvider):
    """直接获取 YouTube 字幕并转换为 SRT。"""

    name = "youtube"
    _VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")

    def __init__(self, preferred_langs: Optional[Sequence[str]] = None, config: Optional[Config] = None):
        self.preferred_langs = [lang.strip() for lang in (preferred_langs or []) if str(lang).strip()]
        self.config = config or Config()

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

    def _subtitle_lang_candidates(self, source_lang: str) -> List[str]:
        candidates = self._build_lang_candidates(source_lang)
        if not candidates:
            return ["en"]
        return candidates

    @staticmethod
    def _clean_caption_text(text: str) -> str:
        return re.sub(r"\s+", " ", html.unescape(str(text or ""))).strip()

    @classmethod
    def _strip_overlap_prefix(cls, previous_text: str, current_text: str) -> str:
        prev_tokens_raw = [token for token in str(previous_text or "").split() if token]
        curr_tokens_raw = [token for token in str(current_text or "").split() if token]
        if not curr_tokens_raw:
            return ""
        if not prev_tokens_raw:
            return " ".join(curr_tokens_raw)

        prev_tokens = [cls._clean_caption_text(token).lower() for token in prev_tokens_raw]
        curr_tokens = [cls._clean_caption_text(token).lower() for token in curr_tokens_raw]
        max_overlap = min(len(prev_tokens), len(curr_tokens))
        overlap = 0
        for size in range(max_overlap, 0, -1):
            if prev_tokens[-size:] == curr_tokens[:size]:
                overlap = size
                break
        if overlap >= len(curr_tokens_raw):
            return ""
        return " ".join(curr_tokens_raw[overlap:]).strip()

    @classmethod
    def _normalize_caption_entries(cls, entries: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        previous_full_text = ""

        for entry in entries:
            full_text = cls._clean_caption_text(entry.get("text", ""))
            if not full_text:
                continue

            novel_text = cls._strip_overlap_prefix(previous_full_text, full_text)
            previous_full_text = full_text
            if not novel_text:
                continue

            start = float(entry.get("start", 0.0) or 0.0)
            end = float(entry.get("end", start + 1.0) or (start + 1.0))
            end = max(end, start + 0.08)

            if normalized and normalized[-1]["text"] == novel_text and start <= normalized[-1]["end"] + 0.12:
                normalized[-1]["end"] = max(normalized[-1]["end"], end)
                continue

            normalized.append({"start": start, "end": end, "text": novel_text})

        # YouTube 自动滚动字幕常见“文本已去重但时间轴仍重叠”的情况。
        # 为了避免最终烧录时多条字幕同时堆叠，这里将相邻 cue 裁平为不重叠。
        for idx in range(len(normalized) - 1):
            current = normalized[idx]
            next_entry = normalized[idx + 1]
            next_start = float(next_entry.get("start", current["end"]) or current["end"])
            if current["end"] > next_start and next_start > current["start"]:
                current["end"] = next_start

        return normalized

    @classmethod
    def _entries_to_srt(cls, entries: Sequence[Dict[str, Any]]) -> str:
        blocks: List[str] = []
        for index, entry in enumerate(entries, start=1):
            text = cls._clean_caption_text(entry.get("text", ""))
            if not text:
                continue
            start = float(entry.get("start", 0.0) or 0.0)
            end = float(entry.get("end", start + 1.0) or (start + 1.0))
            end = max(end, start + 0.08)
            blocks.append(
                f"{index}\n{_format_srt_time(start)} --> {_format_srt_time(end)}\n{text}\n"
            )
        return "\n".join(blocks).strip() + ("\n" if blocks else "")

    @classmethod
    def _parse_srv3_entries(cls, content: str) -> List[Dict[str, Any]]:
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return []

        body = root.find("body")
        if body is None:
            return []

        entries: List[Dict[str, Any]] = []
        for node in body.findall("p"):
            start_ms = int(str(node.attrib.get("t", "0") or "0"))
            duration_ms = int(str(node.attrib.get("d", "0") or "0"))
            text = cls._clean_caption_text("".join(node.itertext()))
            if not text:
                continue
            start = start_ms / 1000.0
            end = (start_ms + max(duration_ms, 80)) / 1000.0
            entries.append({"start": start, "end": end, "text": text})
        return cls._normalize_caption_entries(entries)

    @classmethod
    def _parse_text_cue_entries(cls, content: str) -> List[Dict[str, Any]]:
        normalized = (content or "").replace("\r\n", "\n").replace("\ufeff", "").strip()
        if not normalized:
            return []

        entries: List[Dict[str, Any]] = []
        blocks = [block.strip() for block in normalized.split("\n\n") if block.strip()]
        for block in blocks:
            lines = [line.strip() for line in block.split("\n") if line.strip()]
            if not lines:
                continue

            timing_index = next((idx for idx, line in enumerate(lines) if "-->" in line), -1)
            if timing_index == -1:
                continue

            timing_line = lines[timing_index]
            start_raw, end_raw = [part.strip() for part in timing_line.split("-->", 1)]
            end_raw = end_raw.split()[0]
            try:
                start = _parse_caption_timestamp(start_raw)
                end = _parse_caption_timestamp(end_raw)
            except ValueError:
                continue

            text = cls._clean_caption_text(" ".join(lines[timing_index + 1 :]))
            if not text:
                continue
            entries.append({"start": start, "end": end, "text": text})

        return cls._normalize_caption_entries(entries)

    @classmethod
    def _load_ytdlp_subtitle_content(cls, subtitle_path: Path) -> str:
        raw = subtitle_path.read_text(encoding="utf-8", errors="ignore")
        suffix = subtitle_path.suffix.lower()
        if suffix == ".srv3":
            entries = cls._parse_srv3_entries(raw)
        else:
            entries = cls._parse_text_cue_entries(raw)
        return cls._entries_to_srt(entries)

    def _cookies_file(self) -> Path:
        working_dir = Path(
            self.config.get("storage", "local", "mac_working_dir", default="/tmp/video-factory/working")
        )
        return working_dir.parent / "config" / "youtube_cookies.txt"

    async def _download_subtitles_via_ytdlp(self, video_url: str, source_lang: str) -> Optional[str]:
        temp_dir = Path(tempfile.mkdtemp(prefix="vf_asr_ytdlp_subs_"))
        output_template = str(temp_dir / "%(id)s.%(ext)s")
        base_cmd: List[str]
        try:
            base_cmd = build_ytdlp_base_cmd()
        except FileNotFoundError as exc:
            logger.warning("yt-dlp 自动字幕抓取不可用: %s", exc)
            return None

        cmd = list(base_cmd)
        cookies_file = self._cookies_file()
        if cookies_file.exists():
            cmd.extend(["--cookies", str(cookies_file)])
        cmd.extend([
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-format",
            "srv3/vtt/best",
            "--sub-langs",
            ",".join(self._subtitle_lang_candidates(source_lang)),
            "--no-playlist",
            "-o",
            output_template,
            video_url,
        ])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning("yt-dlp 自动字幕抓取失败: %s", stderr.decode(errors="ignore")[-400:])
                return None

            candidates = sorted(temp_dir.glob("*.srv3")) + sorted(temp_dir.glob("*.vtt")) + sorted(temp_dir.glob("*.srt"))
            if not candidates:
                return None

            preferred_suffixes: List[str] = []
            for lang in self._subtitle_lang_candidates(source_lang):
                preferred_suffixes.extend([f".{lang}.srv3", f".{lang}.vtt", f".{lang}.srt"])
            for suffix in preferred_suffixes:
                for path in candidates:
                    if path.name.endswith(suffix):
                        content = type(self)._load_ytdlp_subtitle_content(path).strip()
                        return content + ("\n" if content else "")

            content = type(self)._load_ytdlp_subtitle_content(candidates[0]).strip()
            return content + ("\n" if content else "")
        except Exception as exc:
            logger.warning("yt-dlp 自动字幕抓取异常: %s", exc)
            return None
        finally:
            try:
                for path in temp_dir.glob("*"):
                    if path.is_file():
                        path.unlink()
                temp_dir.rmdir()
            except Exception:
                pass

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
            items = []

        if not items:
            logger.info("YouTube Transcript API 字幕为空，尝试 yt-dlp 自动字幕: video_id=%s", video_id)
            srt_fallback = await self._download_subtitles_via_ytdlp(video_url, source_lang)
            if srt_fallback and srt_fallback.strip():
                logger.info("✅ yt-dlp 自动字幕抓取成功: video_id=%s", video_id)
                return ASRResult(
                    srt_content=srt_fallback,
                    method=self.name,
                    source_lang=source_lang,
                    metadata={"video_id": video_id, "source": "yt-dlp"},
                )
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
