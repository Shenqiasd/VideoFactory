"""
封面生成模块
- 从视频中提取关键帧作为封面
- 支持横版/竖版封面
"""
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class CoverGenerator:
    """
    视频封面生成器
    从视频中提取高质量关键帧作为封面
    """

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg = ffmpeg_path

    async def extract_keyframes(
        self,
        video_path: str,
        output_dir: str,
        count: int = 5,
        format: str = "jpg",
        quality: int = 2,
    ) -> List[str]:
        """
        提取关键帧

        Args:
            video_path: 视频路径
            output_dir: 输出目录
            count: 提取帧数
            format: 图片格式（jpg/png）
            quality: JPEG质量（1-31, 越小越好）

        Returns:
            List[str]: 提取的图片路径列表
        """
        os.makedirs(output_dir, exist_ok=True)

        # 获取视频时长
        duration = await self._get_duration(video_path)
        if not duration or duration <= 0:
            duration = 600  # 默认10分钟

        # 在视频的10%-90%范围内均匀取帧（避免片头片尾）
        start_pct = 0.10
        end_pct = 0.90
        interval = (end_pct - start_pct) * duration / count

        output_paths = []

        for i in range(count):
            timestamp = start_pct * duration + i * interval
            output_path = os.path.join(output_dir, f"cover_{i+1:02d}.{format}")

            cmd = [
                self.ffmpeg,
                "-ss", f"{timestamp:.2f}",
                "-i", video_path,
                "-vframes", "1",
                "-q:v", str(quality),
                "-y",
                output_path
            ]

            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await asyncio.wait_for(process.communicate(), timeout=30)

                if process.returncode == 0 and os.path.exists(output_path):
                    output_paths.append(output_path)
                    logger.debug(f"提取帧: {timestamp:.1f}秒 → {output_path}")

            except asyncio.TimeoutError:
                logger.warning(f"提取帧超时: {timestamp:.1f}秒")
            except Exception as e:
                logger.warning(f"提取帧异常: {e}")

        logger.info(f"🖼️ 提取了 {len(output_paths)}/{count} 张关键帧")
        return output_paths

    async def select_best_frame(self, frame_paths: List[str]) -> Optional[str]:
        """
        选择最佳帧（基于文件大小启发式 — 通常细节丰富的帧文件更大）

        Args:
            frame_paths: 帧图片路径列表

        Returns:
            Optional[str]: 最佳帧路径
        """
        if not frame_paths:
            return None

        best = max(frame_paths, key=lambda p: os.path.getsize(p) if os.path.exists(p) else 0)
        logger.info(f"🖼️ 选择最佳封面: {best} ({os.path.getsize(best)/1024:.0f} KB)")
        return best

    async def create_horizontal_cover(
        self,
        frame_path: str,
        output_path: str,
        width: int = 1920,
        height: int = 1080,
    ) -> bool:
        """
        生成横版封面（16:9）

        Args:
            frame_path: 源帧图片路径
            output_path: 输出路径
            width: 目标宽度
            height: 目标高度

        Returns:
            bool: 是否成功
        """
        return await self._resize_cover(frame_path, output_path, width, height)

    async def create_vertical_cover(
        self,
        frame_path: str,
        output_path: str,
        width: int = 1080,
        height: int = 1920,
    ) -> bool:
        """
        生成竖版封面（9:16）

        Args:
            frame_path: 源帧图片路径
            output_path: 输出路径
            width: 目标宽度
            height: 目标高度

        Returns:
            bool: 是否成功
        """
        return await self._resize_cover(frame_path, output_path, width, height)

    async def _resize_cover(
        self,
        input_path: str,
        output_path: str,
        width: int,
        height: int,
    ) -> bool:
        """调整封面尺寸"""
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}"
        )

        cmd = [
            self.ffmpeg,
            "-i", input_path,
            "-vf", vf,
            "-q:v", "2",
            "-y",
            output_path
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(process.communicate(), timeout=30)
            return process.returncode == 0 and os.path.exists(output_path)
        except Exception as e:
            logger.error(f"封面调整异常: {e}")
            return False

    async def _get_duration(self, video_path: str) -> Optional[float]:
        """获取视频时长"""
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            video_path
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            return float(stdout.decode().strip())
        except Exception:
            return None

    async def process(
        self,
        video_path: str,
        output_dir: str,
        generate_vertical: bool = True,
    ) -> Dict[str, str]:
        """
        完整的封面生成流程

        Args:
            video_path: 视频路径
            output_dir: 输出目录
            generate_vertical: 是否生成竖版封面

        Returns:
            Dict[str, str]: {"horizontal": "path", "vertical": "path", "best_frame": "path"}
        """
        os.makedirs(output_dir, exist_ok=True)
        results = {}

        # 提取关键帧
        frames = await self.extract_keyframes(video_path, output_dir, count=8)

        if not frames:
            logger.warning("⚠️ 未能提取到关键帧")
            return results

        # 选择最佳帧
        best_frame = await self.select_best_frame(frames)
        if best_frame:
            results["best_frame"] = best_frame

            # 生成横版封面
            h_cover = os.path.join(output_dir, "cover_horizontal.jpg")
            if await self.create_horizontal_cover(best_frame, h_cover):
                results["horizontal"] = h_cover
                logger.info(f"✅ 横版封面: {h_cover}")

            # 生成竖版封面
            if generate_vertical:
                v_cover = os.path.join(output_dir, "cover_vertical.jpg")
                if await self.create_vertical_cover(best_frame, v_cover):
                    results["vertical"] = v_cover
                    logger.info(f"✅ 竖版封面: {v_cover}")

        return results
