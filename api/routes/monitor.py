"""
频道监控路由 - 管理YouTube频道自动监控
"""
import logging
import time
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.task import TaskStore
from source.youtube_monitor import YouTubeMonitor, MonitoredChannel

logger = logging.getLogger(__name__)
router = APIRouter()

_task_store: Optional[TaskStore] = None
_monitor: Optional[YouTubeMonitor] = None


def get_task_store() -> TaskStore:
    global _task_store
    if _task_store is None:
        _task_store = TaskStore()
    return _task_store


def get_monitor() -> YouTubeMonitor:
    global _monitor
    if _monitor is None:
        _monitor = YouTubeMonitor()
    return _monitor


class ChannelCreateRequest(BaseModel):
    """添加监控频道请求"""
    channel_id: str = Field(..., description="YouTube频道ID")
    name: str = Field("", description="频道名称")
    enabled: bool = Field(True, description="是否启用")
    check_interval: int = Field(3600, description="检查间隔（秒）")
    default_scope: str = Field("full", description="默认任务范围")
    default_source_lang: str = Field("en", description="默认源语言")
    default_target_lang: str = Field("zh_cn", description="默认目标语言")
    default_priority: int = Field(2, description="默认优先级 (0=紧急 1=高 2=普通 3=低)")
    max_video_duration: int = Field(0, description="最大视频时长（秒），0=不限制")
    min_video_duration: int = Field(0, description="最小视频时长（秒）")


class ToggleRequest(BaseModel):
    """启用/禁用请求"""
    enabled: bool = Field(..., description="是否启用")


@router.post("/channels")
async def add_channel(request: ChannelCreateRequest):
    """添加监控频道"""
    monitor = get_monitor()

    # 检查是否已存在
    existing = monitor.get_channel(request.channel_id)
    if existing:
        raise HTTPException(
            status_code=400,
            detail={"code": "CHANNEL_EXISTS", "message": f"频道已存在: {request.channel_id}"},
        )

    channel = monitor.add_channel(
        channel_id=request.channel_id,
        name=request.name,
        check_interval=request.check_interval,
        default_scope=request.default_scope,
        default_source_lang=request.default_source_lang,
        default_target_lang=request.default_target_lang,
        default_priority=request.default_priority,
        max_video_duration=request.max_video_duration,
        min_video_duration=request.min_video_duration,
    )

    return {
        "message": "频道已添加",
        "channel": channel.to_dict(),
    }


@router.get("/channels")
async def list_channels():
    """列出所有监控频道"""
    monitor = get_monitor()
    return {
        "channels": [ch.to_dict() for ch in monitor.channels],
        "total": len(monitor.channels),
    }


@router.delete("/channels/{channel_id}")
async def remove_channel(channel_id: str):
    """移除监控频道"""
    monitor = get_monitor()

    existing = monitor.get_channel(channel_id)
    if not existing:
        raise HTTPException(
            status_code=404,
            detail={"code": "CHANNEL_NOT_FOUND", "message": f"频道不存在: {channel_id}"},
        )

    monitor.remove_channel(channel_id)
    return {
        "message": "频道已移除",
        "channel_id": channel_id,
    }


@router.post("/channels/{channel_id}/toggle")
async def toggle_channel(channel_id: str, request: ToggleRequest):
    """启用/禁用监控频道"""
    monitor = get_monitor()

    channel = monitor.toggle_channel(channel_id, request.enabled)
    if not channel:
        raise HTTPException(
            status_code=404,
            detail={"code": "CHANNEL_NOT_FOUND", "message": f"频道不存在: {channel_id}"},
        )

    return {
        "message": f"频道已{'启用' if request.enabled else '禁用'}",
        "channel": channel.to_dict(),
    }


@router.post("/channels/{channel_id}/check")
async def check_channel(channel_id: str):
    """手动触发频道检查"""
    monitor = get_monitor()
    task_store = get_task_store()

    channel = monitor.get_channel(channel_id)
    if not channel:
        raise HTTPException(
            status_code=404,
            detail={"code": "CHANNEL_NOT_FOUND", "message": f"频道不存在: {channel_id}"},
        )

    new_videos = await monitor.check_channel(channel_id)
    channel.last_checked_at = time.time()

    created_tasks = []
    for video in new_videos:
        video_url = video.get("url", "")
        if not video_url:
            continue

        if monitor.is_duplicate(video_url, task_store):
            continue

        # 时长过滤
        duration = video.get("duration", 0) or 0
        if channel.max_video_duration > 0 and duration > channel.max_video_duration:
            continue
        if channel.min_video_duration > 0 and duration < channel.min_video_duration:
            continue

        task = task_store.create(
            source_url=video_url,
            source_title=video.get("title", ""),
            source_lang=channel.default_source_lang,
            target_lang=channel.default_target_lang,
            task_scope=channel.default_scope,
            priority=channel.default_priority,
        )
        monitor.mark_seen(video_url)
        monitor._auto_created_count += 1
        created_tasks.append(task.task_id)

    channel.consecutive_failures = 0
    monitor._save_state()

    return {
        "message": f"检查完成，发现 {len(new_videos)} 个视频，创建 {len(created_tasks)} 个任务",
        "channel_id": channel_id,
        "new_videos_found": len(new_videos),
        "tasks_created": len(created_tasks),
        "task_ids": created_tasks,
    }


@router.get("/status")
async def get_monitor_status():
    """获取监控概览"""
    monitor = get_monitor()

    enabled_channels = [ch for ch in monitor.channels if ch.enabled]
    last_check_times = {}
    for ch in monitor.channels:
        if ch.last_checked_at > 0:
            last_check_times[ch.channel_id] = ch.last_checked_at

    return {
        "total_channels": len(monitor.channels),
        "enabled_channels": len(enabled_channels),
        "last_check_times": last_check_times,
        "total_auto_created_tasks": monitor._auto_created_count,
        "seen_videos_count": len(monitor._seen_videos),
    }
