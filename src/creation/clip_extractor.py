from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import List

from creation.utils import extract_subtitle_window, parse_srt_file, write_srt_entries

logger = logging.getLogger(__name__)


class ClipExtractor:
    """提取片段视频与对应字幕。"""

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg = ffmpeg_path

    async def _run_ffmpeg(self, args: List[str], timeout: int = 900) -> bool:
        cmd = [self.ffmpeg] + args
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            logger.error("片段提取超时")
            return False

        if process.returncode != 0:
            logger.error("片段提取失败: %s", stderr.decode(errors="ignore")[-400:])
            return False
        return True

    async def extract_video(
        self,
        video_path: str,
        start: float,
        end: float,
        output_path: str,
    ) -> bool:
        duration = max(0.1, end - start)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        args = [
            "-ss", f"{start:.3f}",
            "-i", video_path,
            "-t", f"{duration:.3f}",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-y",
            output_path,
        ]
        return await self._run_ffmpeg(args)

    def extract_subtitles(
        self,
        subtitle_path: str,
        start: float,
        end: float,
        output_path: str,
    ) -> str:
        entries = parse_srt_file(subtitle_path)
        if not entries:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text("", encoding="utf-8")
            return output_path

        segment_entries = extract_subtitle_window(entries, start, end)
        write_srt_entries(segment_entries, output_path)
        return output_path
