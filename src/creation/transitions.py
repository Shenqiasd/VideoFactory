from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from factory.long_video import LongVideoProcessor

logger = logging.getLogger(__name__)


class TransitionComposer:
    """简单转场：对成片做首尾 fade，缺省时直接复制。"""

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg = ffmpeg_path
        self.processor = LongVideoProcessor(ffmpeg_path=ffmpeg_path)

    async def apply(
        self,
        video_path: str,
        output_path: str,
        *,
        transition: str = "fade",
        duration: float = 0.35,
    ) -> bool:
        if transition != "fade" or duration <= 0:
            shutil.copy2(video_path, output_path)
            return True

        info = await self.processor.get_video_info(video_path)
        total_duration = float((info or {}).get("duration", 0.0) or 0.0)
        if total_duration <= duration * 2:
            shutil.copy2(video_path, output_path)
            return True

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fade_out_start = max(0.0, total_duration - duration)
        cmd = [
            self.ffmpeg,
            "-i", video_path,
            "-vf", f"fade=t=in:st=0:d={duration:.2f},fade=t=out:st={fade_out_start:.2f}:d={duration:.2f}",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "copy",
            "-y",
            output_path,
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            logger.warning("转场应用失败，回退原文件: %s", stderr.decode(errors="ignore")[-300:])
            shutil.copy2(video_path, output_path)
        return True
