"""
封面生成模块
- 从视频中提取关键帧作为封面
- 支持横版/竖版封面
"""
import asyncio
import mimetypes
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.parse import parse_qs, urlparse

import httpx

from source.downloader import VideoDownloader

logger = logging.getLogger(__name__)


class CoverGenerator:
    """
    视频封面生成器
    从视频中提取高质量关键帧作为封面
    """

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg = ffmpeg_path

    @staticmethod
    def _prepare_output_dir(output_dir: str):
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        for child in path.iterdir():
            if child.is_file() or child.is_symlink():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                shutil.rmtree(child, ignore_errors=True)

    @staticmethod
    def _extract_youtube_video_id(source_url: str) -> str:
        parsed = urlparse(str(source_url or "").strip())
        host = parsed.netloc.lower()
        if "youtu.be" in host:
            return parsed.path.strip("/").split("/")[0]
        if "youtube.com" in host:
            video_id = parse_qs(parsed.query).get("v", [""])[0].strip()
            if video_id:
                return video_id
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live", "watch"}:
                return parts[1].strip()
        return ""

    @classmethod
    def _is_youtube_url(cls, source_url: str) -> bool:
        return bool(cls._extract_youtube_video_id(source_url))

    @staticmethod
    def _thumbnail_suffix(url: str, content_type: str) -> str:
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
            return suffix
        guessed = mimetypes.guess_extension((content_type or "").split(";")[0].strip().lower()) or ""
        if guessed in {".jpe", ".jpeg"}:
            return ".jpg"
        if guessed in {".jpg", ".png", ".webp"}:
            return guessed
        return ".jpg"

    async def _download_source_thumbnail(self, source_url: str, output_dir: str) -> Optional[str]:
        if not self._is_youtube_url(source_url):
            return None

        candidates: List[str] = []
        try:
            info = await VideoDownloader().get_video_info(source_url, timeout=60)
            thumbnail = str((info or {}).get("thumbnail") or "").strip()
            if thumbnail:
                candidates.append(thumbnail)
        except Exception as exc:
            logger.warning("获取 YouTube 缩略图信息失败，尝试 CDN 回退: %s", exc)

        video_id = self._extract_youtube_video_id(source_url)
        if video_id:
            candidates.extend(
                [
                    f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
                    f"https://i.ytimg.com/vi/{video_id}/sddefault.jpg",
                    f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                    f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
                    f"https://i.ytimg.com/vi/{video_id}/default.jpg",
                ]
            )

        seen = set()
        unique_candidates = []
        for url in candidates:
            if not url or url in seen:
                continue
            seen.add(url)
            unique_candidates.append(url)

        if not unique_candidates:
            return None

        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            for thumbnail_url in unique_candidates:
                try:
                    response = await client.get(thumbnail_url)
                except Exception as exc:
                    logger.warning("下载缩略图失败: %s", exc)
                    continue
                content_type = response.headers.get("content-type", "")
                if response.status_code != 200 or not response.content or not content_type.startswith("image/"):
                    continue
                suffix = self._thumbnail_suffix(thumbnail_url, content_type)
                cover_path = Path(output_dir) / f"cover_horizontal{suffix}"
                cover_path.write_bytes(response.content)
                logger.info("🖼️ 使用 YouTube 原始缩略图作为封面: %s", thumbnail_url)
                return str(cover_path)

        return None

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
        source_url: str = "",
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
        self._prepare_output_dir(output_dir)
        results: Dict[str, str] = {}

        thumbnail_cover = await self._download_source_thumbnail(source_url, output_dir)
        if thumbnail_cover:
            results["horizontal"] = thumbnail_cover
            if generate_vertical:
                v_cover = os.path.join(output_dir, "cover_vertical.jpg")
                if await self.create_vertical_cover(thumbnail_cover, v_cover):
                    results["vertical"] = v_cover
            return results

        frame_dir = tempfile.mkdtemp(prefix="vf_cover_frames_")
        frames: List[str] = []
        try:
            frames = await self.extract_keyframes(video_path, frame_dir, count=8)

            if not frames:
                logger.warning("⚠️ 未能提取到关键帧")
                return results

            best_frame = await self.select_best_frame(frames)
            if not best_frame:
                return results

            h_cover = os.path.join(output_dir, "cover_horizontal.jpg")
            if await self.create_horizontal_cover(best_frame, h_cover):
                results["horizontal"] = h_cover
                logger.info(f"✅ 横版封面: {h_cover}")

            if generate_vertical:
                v_cover = os.path.join(output_dir, "cover_vertical.jpg")
                if await self.create_vertical_cover(best_frame, v_cover):
                    results["vertical"] = v_cover
                    logger.info(f"✅ 竖版封面: {v_cover}")
        finally:
            shutil.rmtree(frame_dir, ignore_errors=True)

        return results
