from __future__ import annotations

import os
import shutil

from factory.long_video import LongVideoProcessor


class IntroOutroComposer:
    """片头片尾拼接。"""

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.processor = LongVideoProcessor(ffmpeg_path=ffmpeg_path)

    async def compose(
        self,
        video_path: str,
        output_path: str,
        *,
        intro_path: str = "",
        outro_path: str = "",
    ) -> bool:
        if not intro_path and not outro_path:
            shutil.copy2(video_path, output_path)
            return True
        return await self.processor.add_intro_outro(
            video_path,
            output_path,
            intro_path=intro_path if intro_path and os.path.exists(intro_path) else None,
            outro_path=outro_path if outro_path and os.path.exists(outro_path) else None,
        )
