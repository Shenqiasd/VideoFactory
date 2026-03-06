"""
YouTube频道监控 - 检测新视频并自动触发处理
优先级：低（当前由用户手动提供内容）
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Set

logger = logging.getLogger(__name__)


class YouTubeMonitor:
    """
    YouTube频道监控器
    定期检查指定频道的新视频

    注意：当前优先级较低，用户手动提交为主。
    此模块为未来自动化预留。
    """

    def __init__(self, check_interval: int = 3600, state_file: str = None):
        """
        Args:
            check_interval: 检查间隔（秒），默认1小时
            state_file: 状态文件路径（记录已处理的视频）
        """
        self.check_interval = check_interval
        self.channels: List[Dict[str, str]] = []  # [{"channel_id": "xxx", "name": "xxx"}]
        self._seen_videos: Set[str] = set()
        self._running = False

        if state_file is None:
            state_file = str(Path.home() / ".video-factory" / "monitor_state.json")
        self.state_file = Path(state_file)
        self._load_state()

    def add_channel(self, channel_id: str, name: str = ""):
        """添加监控频道"""
        self.channels.append({"channel_id": channel_id, "name": name})
        logger.info(f"📺 添加监控频道: {name or channel_id}")

    def remove_channel(self, channel_id: str):
        """移除监控频道"""
        self.channels = [c for c in self.channels if c["channel_id"] != channel_id]

    async def check_channel(self, channel_id: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """
        检查频道的最新视频

        Args:
            channel_id: YouTube频道ID
            max_results: 最大返回数量

        Returns:
            List[Dict]: 新视频列表
        """
        cmd = [
            "yt-dlp",
            "--flat-playlist",
            "--dump-json",
            "--playlist-end", str(max_results),
            f"https://www.youtube.com/channel/{channel_id}/videos"
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=120
            )

            if process.returncode != 0:
                logger.error(f"检查频道失败 {channel_id}: {stderr.decode(errors='ignore')}")
                return []

            new_videos = []
            for line in stdout.decode().strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    info = json.loads(line)
                    video_id = info.get("id", "")
                    if video_id and video_id not in self._seen_videos:
                        new_videos.append({
                            "video_id": video_id,
                            "title": info.get("title", ""),
                            "url": info.get("url", f"https://www.youtube.com/watch?v={video_id}"),
                            "duration": info.get("duration", 0),
                            "channel_id": channel_id,
                        })
                except json.JSONDecodeError:
                    continue

            return new_videos

        except asyncio.TimeoutError:
            logger.error(f"检查频道超时: {channel_id}")
            return []
        except Exception as e:
            logger.error(f"检查频道异常: {e}")
            return []

    async def check_all_channels(self) -> List[Dict[str, Any]]:
        """检查所有频道的新视频"""
        all_new = []
        for channel in self.channels:
            new_videos = await self.check_channel(channel["channel_id"])
            for video in new_videos:
                video["channel_name"] = channel.get("name", "")
            all_new.extend(new_videos)

        if all_new:
            logger.info(f"📺 发现 {len(all_new)} 个新视频")

        return all_new

    def mark_seen(self, video_id: str):
        """标记视频为已处理"""
        self._seen_videos.add(video_id)
        self._save_state()

    async def run_loop(self, callback=None):
        """
        运行监控循环

        Args:
            callback: 发现新视频时的回调函数，接收视频列表
        """
        self._running = True
        logger.info(f"🔄 YouTube监控启动，间隔: {self.check_interval}秒，频道数: {len(self.channels)}")

        while self._running:
            try:
                new_videos = await self.check_all_channels()

                if new_videos and callback:
                    await callback(new_videos)

                # 标记已见
                for video in new_videos:
                    self.mark_seen(video["video_id"])

            except Exception as e:
                logger.error(f"监控循环异常: {e}")

            await asyncio.sleep(self.check_interval)

    def stop(self):
        """停止监控"""
        self._running = False

    def _load_state(self):
        """加载状态"""
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                self._seen_videos = set(data.get("seen_videos", []))
                self.channels = data.get("channels", [])
            except Exception as e:
                logger.warning(f"加载监控状态失败: {e}")

    def _save_state(self):
        """保存状态"""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "seen_videos": list(self._seen_videos),
                "channels": self.channels,
            }
            self.state_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning(f"保存监控状态失败: {e}")
