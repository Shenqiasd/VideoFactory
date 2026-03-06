"""
短视频切片模块
- 从长视频中提取高光片段
- 裁剪为竖屏（9:16）
- 生成适合抖音/小红书/视频号的短视频
"""
import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger(__name__)


class ShortClipExtractor:
    """
    短视频切片器
    从翻译后的长视频中提取高光片段
    """

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg = ffmpeg_path

    async def _run_ffmpeg(self, args: List[str], timeout: int = 300) -> bool:
        """执行ffmpeg命令"""
        cmd = [self.ffmpeg] + args
        logger.info(f"✂️ FFmpeg: {' '.join(cmd[:10])}...")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
            if process.returncode != 0:
                logger.error(f"FFmpeg失败: {stderr.decode(errors='ignore')[-300:]}")
                return False
            return True
        except asyncio.TimeoutError:
            process.kill()
            logger.error(f"FFmpeg超时 ({timeout}秒)")
            return False

    def parse_srt_timestamps(self, srt_path: str) -> List[Dict[str, Any]]:
        """
        解析SRT字幕文件，提取时间戳和文本

        Args:
            srt_path: SRT文件路径

        Returns:
            List[Dict]: [{"index": int, "start": float, "end": float, "text": str}, ...]
        """
        entries = []
        if not os.path.exists(srt_path):
            return entries

        content = Path(srt_path).read_text(encoding="utf-8", errors="ignore")

        # SRT格式解析
        pattern = re.compile(
            r'(\d+)\s*\n'
            r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\n'
            r'(.*?)(?=\n\n|\n\d+\s*\n|\Z)',
            re.DOTALL
        )

        for match in pattern.finditer(content):
            index = int(match.group(1))
            start_time = self._srt_time_to_seconds(match.group(2))
            end_time = self._srt_time_to_seconds(match.group(3))
            text = match.group(4).strip()

            entries.append({
                "index": index,
                "start": start_time,
                "end": end_time,
                "text": text,
            })

        return entries

    def _srt_time_to_seconds(self, time_str: str) -> float:
        """将SRT时间格式转为秒数"""
        parts = time_str.replace(",", ".").split(":")
        hours = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2])
        return hours * 3600 + minutes * 60 + seconds

    def find_highlight_segments(
        self,
        srt_entries: List[Dict],
        min_duration: float = 30.0,
        max_duration: float = 90.0,
        max_clips: int = 5,
    ) -> List[Tuple[float, float, str]]:
        """
        基于字幕密度和文本长度寻找高光片段

        策略：
        1. 将视频分成等长的窗口
        2. 计算每个窗口的字幕密度（字幕数/时长）
        3. 选择密度最高的窗口作为高光
        4. 确保片段边界对齐字幕时间戳

        Args:
            srt_entries: 字幕条目列表
            min_duration: 最短片段时长（秒）
            max_duration: 最长片段时长（秒）
            max_clips: 最大片段数

        Returns:
            List[Tuple]: [(start_sec, end_sec, label), ...]
        """
        if not srt_entries:
            return []

        total_duration = srt_entries[-1]["end"]

        # 如果视频太短，整段作为一个片段
        if total_duration <= max_duration:
            return [(0, total_duration, "full_video")]

        # 计算滑动窗口的字幕密度
        target_duration = (min_duration + max_duration) / 2
        step = max(10, target_duration / 3)

        windows = []
        t = 0
        while t + min_duration <= total_duration:
            window_end = min(t + target_duration, total_duration)

            # 统计窗口内的字幕数和文本总长度
            entries_in_window = [
                e for e in srt_entries
                if e["start"] >= t and e["end"] <= window_end
            ]
            subtitle_count = len(entries_in_window)
            text_length = sum(len(e["text"]) for e in entries_in_window)

            # 综合得分 = 字幕数量 * 文本密度
            density = subtitle_count * text_length / max(1, window_end - t)

            windows.append({
                "start": t,
                "end": window_end,
                "density": density,
                "subtitle_count": subtitle_count,
            })

            t += step

        # 按密度排序，取前N个
        windows.sort(key=lambda w: w["density"], reverse=True)

        # 去除重叠片段
        selected = []
        for w in windows:
            if len(selected) >= max_clips:
                break

            overlap = False
            for s in selected:
                if not (w["end"] <= s["start"] or w["start"] >= s["end"]):
                    overlap = True
                    break

            if not overlap:
                # 对齐到最近的字幕边界
                start = w["start"]
                end = w["end"]

                # 找最近的字幕开始点
                for e in srt_entries:
                    if abs(e["start"] - start) < 3:
                        start = e["start"]
                        break

                # 找最近的字幕结束点
                for e in reversed(srt_entries):
                    if abs(e["end"] - end) < 3:
                        end = e["end"]
                        break

                # 确保时长在范围内
                duration = end - start
                if duration < min_duration:
                    end = start + min_duration
                elif duration > max_duration:
                    end = start + max_duration

                selected.append({
                    "start": max(0, start - 0.5),  # 提前0.5秒开始
                    "end": min(total_duration, end + 0.5),  # 延后0.5秒结束
                })

        # 按时间排序
        selected.sort(key=lambda s: s["start"])

        result = []
        for i, s in enumerate(selected):
            result.append((s["start"], s["end"], f"clip_{i+1:02d}"))

        return result

    async def extract_clip(
        self,
        video_path: str,
        start: float,
        end: float,
        output_path: str,
        vertical: bool = True,
    ) -> bool:
        """
        提取视频片段

        Args:
            video_path: 源视频路径
            start: 开始时间（秒）
            end: 结束时间（秒）
            output_path: 输出路径
            vertical: 是否裁剪为竖屏9:16

        Returns:
            bool: 是否成功
        """
        duration = end - start

        if vertical:
            # 竖屏裁剪：从中心裁剪为9:16
            # 先缩放高度到1920，再从中心裁剪宽度为1080
            vf = "scale=-2:1920,crop=1080:1920"
        else:
            vf = "scale=-2:1080"

        args = [
            "-ss", f"{start:.2f}",
            "-i", video_path,
            "-t", f"{duration:.2f}",
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-y",
            output_path
        ]

        return await self._run_ffmpeg(args, timeout=300)

    async def process(
        self,
        video_path: str,
        subtitle_path: Optional[str],
        output_dir: str,
        max_clips: int = 5,
        min_duration: float = 30.0,
        max_duration: float = 90.0,
        vertical: bool = True,
    ) -> List[str]:
        """
        完整的短视频生成流程

        Args:
            video_path: 翻译后的视频
            subtitle_path: 字幕路径
            output_dir: 输出目录
            max_clips: 最大片段数
            min_duration: 最短时长
            max_duration: 最长时长
            vertical: 是否竖屏

        Returns:
            List[str]: 生成的短视频路径列表
        """
        os.makedirs(output_dir, exist_ok=True)
        output_clips = []

        # 解析字幕寻找高光
        if subtitle_path and os.path.exists(subtitle_path):
            srt_entries = self.parse_srt_timestamps(subtitle_path)
            segments = self.find_highlight_segments(
                srt_entries,
                min_duration=min_duration,
                max_duration=max_duration,
                max_clips=max_clips,
            )
        else:
            # 没有字幕，按均等间隔切片
            segments = await self._uniform_segments(video_path, max_clips, min_duration, max_duration)

        logger.info(f"✂️ 计划提取 {len(segments)} 个短视频片段")

        for start, end, label in segments:
            clip_name = f"short_{label}.mp4"
            clip_path = os.path.join(output_dir, clip_name)

            success = await self.extract_clip(video_path, start, end, clip_path, vertical=vertical)

            if success and os.path.exists(clip_path):
                output_clips.append(clip_path)
                logger.info(f"✅ 短视频: {clip_name} ({end-start:.0f}秒)")
            else:
                logger.warning(f"⚠️ 短视频提取失败: {clip_name}")

        logger.info(f"✂️ 短视频生成完成: {len(output_clips)}/{len(segments)} 成功")
        return output_clips

    async def _uniform_segments(
        self,
        video_path: str,
        max_clips: int,
        min_duration: float,
        max_duration: float,
    ) -> List[Tuple[float, float, str]]:
        """均等间隔分段"""
        # 获取视频时长
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
            total_duration = float(stdout.decode().strip())
        except Exception:
            total_duration = 600  # 默认10分钟

        target_duration = (min_duration + max_duration) / 2
        num_clips = min(max_clips, int(total_duration / target_duration))

        if num_clips == 0:
            return [(0, min(total_duration, max_duration), "clip_01")]

        interval = total_duration / num_clips
        segments = []
        for i in range(num_clips):
            start = i * interval
            end = min(start + target_duration, total_duration)
            segments.append((start, end, f"clip_{i+1:02d}"))

        return segments
