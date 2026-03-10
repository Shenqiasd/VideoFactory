from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class AudioMixer:
    """混合 BGM，缺省时原样复制。"""

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg = ffmpeg_path

    async def mix(
        self,
        video_path: str,
        output_path: str,
        *,
        bgm_path: str = "",
        bgm_volume: float = 0.18,
        original_volume: float = 1.0,
    ) -> bool:
        if not bgm_path or not os.path.exists(bgm_path):
            shutil.copy2(video_path, output_path)
            return True

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.ffmpeg,
            "-stream_loop", "-1",
            "-i", bgm_path,
            "-i", video_path,
            "-filter_complex",
            (
                f"[0:a]volume={max(0.0, min(1.0, bgm_volume)):.3f}[bgm];"
                f"[1:a]volume={max(0.0, min(2.0, original_volume)):.3f}[orig];"
                "[orig][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
            ),
            "-map", "1:v:0",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
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
            logger.warning("BGM 混音失败，回退原视频: %s", stderr.decode(errors="ignore")[-300:])
            shutil.copy2(video_path, output_path)
        return True
