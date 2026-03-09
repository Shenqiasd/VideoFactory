"""
发布模块数据模型
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional


@dataclass
class PublishTask:
    """发布任务数据类"""
    id: str
    task_id: str
    video_path: str
    platform: str
    account_id: str
    title: str
    description: str = ""
    tags: List[str] = field(default_factory=list)
    cover_path: str = ""
    publish_time: Optional[str] = None
    status: str = "pending"  # pending / publishing / done / failed
    publish_url: Optional[str] = None
    error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Account:
    """平台账号数据类"""
    id: str
    platform: str
    name: str
    cookie_path: str = ""
    status: str = "active"
    last_test: Optional[str] = None
    created_at: str = ""
