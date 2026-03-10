from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

from factory.long_video import LongVideoProcessor

logger = logging.getLogger(__name__)


class SmartCropper:
    """智能裁剪器；YOLO 可用时利用焦点轨迹，否则退化为中心裁剪。"""

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg = ffmpeg_path
        self.long_video = LongVideoProcessor(ffmpeg_path=ffmpeg_path)

    async def _run_ffmpeg(self, args: List[str], timeout: int = 900) -> bool:
        process = await asyncio.create_subprocess_exec(
            self.ffmpeg,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            logger.error("智能裁剪超时")
            return False
        if process.returncode != 0:
            logger.error("智能裁剪失败: %s", stderr.decode(errors="ignore")[-400:])
            return False
        return True

    @staticmethod
    def _smoothed_center(samples: List[Dict[str, Any]], width: int, height: int) -> Tuple[int, int]:
        if not samples:
            return int(width / 2), int(height / 2)

        centers_x = [sample["bbox"][0] + sample["bbox"][2] / 2 for sample in samples if sample.get("bbox")]
        centers_y = [sample["bbox"][1] + sample["bbox"][3] / 2 for sample in samples if sample.get("bbox")]
        if not centers_x or not centers_y:
            return int(width / 2), int(height / 2)

        avg_x = sum(centers_x) / len(centers_x)
        avg_y = sum(centers_y) / len(centers_y)
        return int(avg_x), int(avg_y)

    @staticmethod
    def _compute_crop_box(width: int, height: int, center_x: int, center_y: int, aspect_ratio: str) -> Tuple[int, int, int, int]:
        if aspect_ratio == "9:16":
            target_ratio = 9 / 16
        else:
            target_ratio = 16 / 9

        src_ratio = width / max(1, height)
        if src_ratio >= target_ratio:
            crop_h = height
            crop_w = int(height * target_ratio)
        else:
            crop_w = width
            crop_h = int(width / target_ratio)

        crop_w = max(2, min(width, crop_w))
        crop_h = max(2, min(height, crop_h))
        x = max(0, min(width - crop_w, int(center_x - crop_w / 2)))
        y = max(0, min(height - crop_h, int(center_y - crop_h / 2)))
        return x, y, crop_w, crop_h

    async def crop(
        self,
        video_path: str,
        output_path: str,
        focus_track: Dict[str, Any],
        *,
        aspect_ratio: str = "9:16",
        target_size: Tuple[int, int] = (1080, 1920),
    ) -> tuple[bool, Dict[str, Any]]:
        info = await self.long_video.get_video_info(video_path)
        width = int((info or {}).get("width", 1920) or 1920)
        height = int((info or {}).get("height", 1080) or 1080)
        samples = list((focus_track or {}).get("samples", []))
        center_x, center_y = self._smoothed_center(samples, width, height)
        x, y, crop_w, crop_h = self._compute_crop_box(width, height, center_x, center_y, aspect_ratio)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        vf = (
            f"crop={crop_w}:{crop_h}:{x}:{y},"
            f"scale={int(target_size[0])}:{int(target_size[1])}"
        )
        ok = await self._run_ffmpeg(
            [
                "-i", video_path,
                "-vf", vf,
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "128k",
                "-y",
                output_path,
            ]
        )
        return ok, {
            "strategy": (focus_track or {}).get("strategy", "center"),
            "focus_class": (focus_track or {}).get("focus_class", "center"),
            "video_width": width,
            "video_height": height,
            "crop_box": {"x": x, "y": y, "width": crop_w, "height": crop_h},
            "samples": samples,
        }
