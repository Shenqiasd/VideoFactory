"""
发布模块数据模型
"""
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional


@dataclass
class PublishTask:
    """发布任务数据类"""
    task_id: str
    platform: str
    video_path: str
    title: str
    description: str = ""
    tags: List[str] = field(default_factory=list)
    cover_path: str = ""
    scheduled_time: float = 0.0
    status: str = "pending"  # pending / publishing / done / failed
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Account:
    """平台账号数据类"""
    platform: str
    account_id: str
    account_name: str = ""
    cookies_path: str = ""
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
