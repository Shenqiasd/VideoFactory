"""
YouTube频道监控 - 检测新视频并自动触发处理
优先级：低（当前由用户手动提供内容）
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Dict, Any, List, Set, TYPE_CHECKING

from source.ytdlp_runtime import build_ytdlp_base_cmd

if TYPE_CHECKING:
    from core.task import TaskStore

logger = logging.getLogger(__name__)


@dataclass
class MonitoredChannel:
    """被监控的YouTube频道"""
    channel_id: str
    name: str = ""
    enabled: bool = True
    check_interval: int = 3600          # 每频道检查间隔（秒）
    default_scope: str = "full"         # 自动创建任务的 task_scope
    default_source_lang: str = "en"
    default_target_lang: str = "zh_cn"
    default_priority: int = 2
    max_video_duration: int = 0         # 0 = 不限制（秒）
    min_video_duration: int = 0
    last_checked_at: float = 0.0
    consecutive_failures: int = 0       # 用于指数退避
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MonitoredChannel":
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


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
            check_interval: 默认检查间隔（秒），默认1小时
            state_file: 状态文件路径（记录已处理的视频）
        """
        self.check_interval = check_interval
        self.channels: List[MonitoredChannel] = []
        self._seen_videos: Set[str] = set()
        self._running = False
        self._auto_created_count: int = 0  # 自动创建任务计数

        if state_file is None:
            state_file = str(Path.home() / ".video-factory" / "monitor_state.json")
        self.state_file = Path(state_file)
        self._load_state()

    def add_channel(self, channel_id: str, name: str = "", **kwargs) -> MonitoredChannel:
        """添加监控频道"""
        channel = MonitoredChannel(
            channel_id=channel_id,
            name=name,
            check_interval=kwargs.get("check_interval", self.check_interval),
            default_scope=kwargs.get("default_scope", "full"),
            default_source_lang=kwargs.get("default_source_lang", "en"),
            default_target_lang=kwargs.get("default_target_lang", "zh_cn"),
            default_priority=kwargs.get("default_priority", 2),
            max_video_duration=kwargs.get("max_video_duration", 0),
            min_video_duration=kwargs.get("min_video_duration", 0),
            created_at=time.time(),
        )
        self.channels.append(channel)
        self._save_state()
        logger.info(f"添加监控频道: {name or channel_id}")
        return channel

    def remove_channel(self, channel_id: str):
        """移除监控频道"""
        self.channels = [c for c in self.channels if c.channel_id != channel_id]
        self._save_state()

    def get_channel(self, channel_id: str) -> Optional[MonitoredChannel]:
        """获取指定频道"""
        for ch in self.channels:
            if ch.channel_id == channel_id:
                return ch
        return None

    def toggle_channel(self, channel_id: str, enabled: bool) -> Optional[MonitoredChannel]:
        """启用/禁用频道"""
        channel = self.get_channel(channel_id)
        if channel:
            channel.enabled = enabled
            self._save_state()
        return channel

    def is_duplicate(self, video_url: str, task_store: "TaskStore") -> bool:
        """
        幂等去重检查

        Args:
            video_url: 视频URL
            task_store: 任务存储

        Returns:
            bool: 是否已存在
        """
        # 快速路径：内存缓存
        if video_url in self._seen_videos:
            return True

        # 可靠路径：检查 TaskStore 中是否存在相同 source_url 的任务
        for task in task_store.list_all():
            if task.source_url == video_url:
                return True

        return False

    def should_check(self, channel: MonitoredChannel) -> bool:
        """
        判断频道是否应该检查（考虑指数退避）

        Args:
            channel: 监控频道

        Returns:
            bool: 是否应该检查
        """
        if not channel.enabled:
            return False

        now = time.time()
        backoff = self._backoff_seconds(channel)
        return (now - channel.last_checked_at) >= backoff

    def _backoff_seconds(self, channel: MonitoredChannel) -> float:
        """
        计算退避时间

        连续失败时：delay = check_interval * min(2^failures, 24)

        Args:
            channel: 监控频道

        Returns:
            float: 退避秒数
        """
        if channel.consecutive_failures <= 0:
            return float(channel.check_interval)

        multiplier = min(2 ** channel.consecutive_failures, 24)
        return float(channel.check_interval * multiplier)

    async def check_channel(self, channel_id: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """
        检查频道的最新视频

        Args:
            channel_id: YouTube频道ID
            max_results: 最大返回数量

        Returns:
            List[Dict]: 新视频列表
        """
        cmd = build_ytdlp_base_cmd() + [
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
            new_videos = await self.check_channel(channel.channel_id)
            for video in new_videos:
                video["channel_name"] = channel.name
            all_new.extend(new_videos)

        if all_new:
            logger.info(f"发现 {len(all_new)} 个新视频")

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
        logger.info(f"YouTube监控启动，间隔: {self.check_interval}秒，频道数: {len(self.channels)}")

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
        """加载状态（兼容旧格式 List[Dict[str,str]] 和新格式 MonitoredChannel）"""
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                self._seen_videos = set(data.get("seen_videos", []))
                self._auto_created_count = data.get("auto_created_count", 0)

                raw_channels = data.get("channels", [])
                self.channels = []
                for ch in raw_channels:
                    if isinstance(ch, dict):
                        # 兼容旧格式：只有 channel_id 和 name
                        if "enabled" not in ch and "check_interval" not in ch:
                            self.channels.append(MonitoredChannel(
                                channel_id=ch.get("channel_id", ""),
                                name=ch.get("name", ""),
                                check_interval=self.check_interval,
                            ))
                        else:
                            self.channels.append(MonitoredChannel.from_dict(ch))
            except Exception as e:
                logger.warning(f"加载监控状态失败: {e}")

    def _save_state(self):
        """保存状态"""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "seen_videos": list(self._seen_videos),
                "channels": [ch.to_dict() for ch in self.channels],
                "auto_created_count": self._auto_created_count,
            }
            self.state_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning(f"保存监控状态失败: {e}")
