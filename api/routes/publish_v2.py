"""
多平台发布 API v2 — 任务队列 + 定时发布。

提供：
- POST   /api/publish/v2/create          创建发布任务（支持多平台）
- GET    /api/publish/v2/tasks            任务列表（带过滤 + 分页）
- GET    /api/publish/v2/tasks/{task_id}  任务详情
- POST   /api/publish/v2/tasks/{task_id}/retry  重试失败任务
- DELETE /api/publish/v2/tasks/{task_id}  取消/删除任务
- GET    /api/publish/v2/stats            发布统计
"""

import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.auth import require_auth
from core.database import Database

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_db: Optional[Database] = None
_publish_queue = None  # set by server.py at startup


def _get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


def set_publish_queue(queue) -> None:
    """Called by server.py to inject the PublishQueue instance."""
    global _publish_queue
    _publish_queue = queue


def _get_queue():
    return _publish_queue


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class PlatformTarget(BaseModel):
    platform: str
    account_id: str
    platform_options: dict = Field(default_factory=dict)


class CreatePublishRequest(BaseModel):
    video_path: str
    title: str
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    cover_path: str = ""
    platforms: List[PlatformTarget]
    scheduled_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/create")
async def create_publish_task(body: CreatePublishRequest):
    """
    创建发布任务（支持同时发布到多个平台）。

    为 platforms 中的每个平台创建一个独立任务，返回所有 task_id。
    """
    queue = _get_queue()
    if queue is None:
        return JSONResponse(
            status_code=503,
            content={"success": False, "detail": "发布队列未初始化"},
        )

    task_ids: list[str] = []
    for target in body.platforms:
        task_data = {
            "id": str(uuid.uuid4()),
            "account_id": target.account_id,
            "platform": target.platform,
            "title": body.title,
            "description": body.description,
            "tags": body.tags,
            "video_path": body.video_path,
            "cover_path": body.cover_path,
            "platform_options": target.platform_options,
            "scheduled_at": body.scheduled_at,
        }
        task_id = await queue.enqueue(task_data)
        task_ids.append(task_id)

    return {"success": True, "data": {"task_ids": task_ids}}


@router.get("/tasks")
async def list_tasks(
    platform: Optional[str] = None,
    status: Optional[str] = None,
    account_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """任务列表（带过滤 + 分页）。"""
    db = _get_db()
    # True total for pagination (unbounded COUNT query)
    total = db.count_publish_tasks_v2(
        platform=platform,
        status=status,
        account_id=account_id,
    )
    # Fetch the page of results
    tasks = db.get_publish_tasks_v2(
        platform=platform,
        status=status,
        account_id=account_id,
        limit=limit + offset,
    )
    # manual offset slicing (DB method doesn't support offset natively)
    paginated = tasks[offset: offset + limit]
    return {
        "success": True,
        "data": {
            "tasks": paginated,
            "total": total,
            "limit": limit,
            "offset": offset,
        },
    }


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """获取单个任务详情。"""
    db = _get_db()
    task = db.get_publish_task_v2(task_id)
    if not task:
        return JSONResponse(
            status_code=404,
            content={"success": False, "detail": "任务不存在"},
        )
    return {"success": True, "data": task}


@router.post("/tasks/{task_id}/retry")
async def retry_task(task_id: str):
    """重试失败的任务。"""
    queue = _get_queue()
    if queue is None:
        return JSONResponse(
            status_code=503,
            content={"success": False, "detail": "发布队列未初始化"},
        )
    ok = await queue.retry_task(task_id)
    if not ok:
        return JSONResponse(
            status_code=400,
            content={"success": False, "detail": "任务不存在或状态不允许重试"},
        )
    return {"success": True, "message": "任务已重新入队"}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """取消/删除任务。"""
    queue = _get_queue()
    db = _get_db()
    task = db.get_publish_task_v2(task_id)
    if not task:
        return JSONResponse(
            status_code=404,
            content={"success": False, "detail": "任务不存在"},
        )

    # For pending/scheduled tasks, cancel via queue
    if queue and task["status"] in ("pending", "scheduled"):
        await queue.cancel_task(task_id)
        return {"success": True, "message": "任务已取消"}

    # For other statuses (failed, cancelled, published), delete directly
    db.delete_publish_task_v2(task_id)
    return {"success": True, "message": "任务已删除"}


@router.get("/stats")
async def get_stats():
    """发布统计（按状态计数）。"""
    db = _get_db()
    statuses = ["pending", "publishing", "published", "failed", "scheduled", "cancelled"]
    counts: dict[str, int] = {}
    for s in statuses:
        counts[s] = db.count_publish_tasks_v2(status=s)
    counts["total"] = sum(counts.values())
    return {"success": True, "data": counts}
