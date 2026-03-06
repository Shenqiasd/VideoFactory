"""
视频下载器 - 封装yt-dlp进行视频下载
"""
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class VideoDownloader:
    """
    视频下载器
    基于yt-dlp，支持YouTube和其他平台
    """

    def __init__(self, download_dir: str = "/tmp/video-factory/downloads", timeout: int = 600):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout

        # YouTube cookies 文件路径
        self.cookies_file = Path(download_dir).parent / "config" / "youtube_cookies.txt"

    async def download(
        self,
        url: str,
        output_name: Optional[str] = None,
        max_height: int = 1080,
        format_spec: Optional[str] = None,
    ) -> Optional[str]:
        """
        下载视频

        Args:
            url: 视频URL
            output_name: 输出文件名（不含扩展名）
            max_height: 最大分辨率高度
            format_spec: yt-dlp格式选择字符串

        Returns:
            Optional[str]: 下载后的文件路径，失败返回None
        """
        if output_name:
            output_template = str(self.download_dir / f"{output_name}.%(ext)s")
        else:
            output_template = str(self.download_dir / "%(title)s.%(ext)s")

        if not format_spec:
            format_spec = f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]"

        cmd = [
            "yt-dlp",
            "-f", format_spec,
            "--merge-output-format", "mp4",
            "-o", output_template,
            "--no-playlist",
            "--write-info-json",
            "--no-overwrites",
        ]

        # 如果 cookies 文件存在，自动使用
        if self.cookies_file.exists():
            cmd.extend(["--cookies", str(self.cookies_file)])
            logger.info(f"🍪 使用 cookies 文件: {self.cookies_file}")

        cmd.append(url)

        try:
            logger.info(f"📥 开始下载: {url}")
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout
            )

            if process.returncode != 0:
                error = stderr.decode(errors="ignore")
                logger.error(f"❌ 下载失败: {error}")
                return None

            # 查找下载后的文件
            stdout_text = stdout.decode(errors="ignore")
            # yt-dlp输出中查找最终文件路径
            for line in stdout_text.split("\n"):
                if "[Merger]" in line and "Merging formats" in line:
                    # 提取输出文件名
                    pass
                if "has already been downloaded" in line or "[download] Destination:" in line:
                    pass

            # 通过glob查找最新文件
            mp4_files = sorted(self.download_dir.glob("*.mp4"), key=os.path.getmtime, reverse=True)
            if mp4_files:
                result_path = str(mp4_files[0])
                logger.info(f"✅ 下载完成: {result_path}")
                return result_path

            logger.error("下载后未找到MP4文件")
            return None

        except asyncio.TimeoutError:
            logger.error(f"⏰ 下载超时 ({self.timeout}秒): {url}")
            return None
        except Exception as e:
            logger.error(f"下载异常: {e}")
            return None

    async def get_video_info(self, url: str) -> Optional[Dict[str, Any]]:
        """
        获取视频元信息（不下载）

        Args:
            url: 视频URL

        Returns:
            Optional[Dict]: 视频信息
        """
        cmd = [
            "yt-dlp",
            "--dump-json",
            "--no-download",
            "--no-playlist",
        ]

        # 如果 cookies 文件存在，自动使用
        if self.cookies_file.exists():
            cmd.extend(["--cookies", str(self.cookies_file)])

        cmd.append(url)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=60
            )

            if process.returncode == 0:
                info = json.loads(stdout.decode())
                return {
                    "title": info.get("title", ""),
                    "description": info.get("description", ""),
                    "duration": info.get("duration", 0),
                    "view_count": info.get("view_count", 0),
                    "like_count": info.get("like_count", 0),
                    "upload_date": info.get("upload_date", ""),
                    "channel": info.get("channel", ""),
                    "channel_id": info.get("channel_id", ""),
                    "thumbnail": info.get("thumbnail", ""),
                    "tags": info.get("tags", []),
                    "categories": info.get("categories", []),
                    "language": info.get("language", ""),
                    "filesize_approx": info.get("filesize_approx", 0),
                    "webpage_url": info.get("webpage_url", url),
                }
            else:
                logger.error(f"获取视频信息失败: {stderr.decode(errors='ignore')}")
                return None

        except Exception as e:
            logger.error(f"获取视频信息异常: {e}")
            return None

    async def batch_download(self, urls: List[str], max_concurrent: int = 2) -> List[Optional[str]]:
        """
        批量下载视频

        Args:
            urls: URL列表
            max_concurrent: 最大并发数

        Returns:
            List[Optional[str]]: 下载路径列表
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _download_with_limit(url: str) -> Optional[str]:
            async with semaphore:
                return await self.download(url)

        tasks = [_download_with_limit(url) for url in urls]
        return await asyncio.gather(*tasks)
