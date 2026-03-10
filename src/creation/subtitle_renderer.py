from __future__ import annotations

import shutil
from typing import Any, Dict, Optional

from factory.long_video import LongVideoProcessor


class SubtitleRenderer:
    """平台化字幕烧录。"""

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.processor = LongVideoProcessor(ffmpeg_path=ffmpeg_path)

    async def render(
        self,
        video_path: str,
        subtitle_path: str,
        output_path: str,
        *,
        subtitle_style: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not subtitle_path:
            shutil.copy2(video_path, output_path)
            return True
        return await self.processor.burn_subtitles(
            video_path=video_path,
            subtitle_path=subtitle_path,
            output_path=output_path,
            subtitle_style=subtitle_style,
        )
