"""
多平台发布 V2 API 路由。

提供发布任务的创建、查询、重试、取消和统计接口。
"""

import uuid
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.auth import require_auth

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------

class PlatformTarget(BaseModel):
    platform: str
    account_id: str
    platform_options: Optional[Dict] = None


class CreatePublishRequest(BaseModel):
    video_path: str
    title: str
    description: str = ""
    tags: List[str] = []
    cover_path: str = ""
    platforms: List[PlatformTarget]
    scheduled_at: Optional[str] = None


class TaskResponse(BaseModel):
    task_ids: List[str]


# ---------------------------------------------------------------------------
# Helper: get queue from app state
# ---------------------------------------------------------------------------

def _get_queue(request: Request):
    queue = getattr(request.app.state, "publish_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="发布队列未初始化")
    return queue


def _get_db(request: Request):
    db = getattr(request.app.state, "publish_db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="数据库未初始化")
    return db


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/create", dependencies=[Depends(require_auth)])
async def create_publish_task(body: CreatePublishRequest, request: Request):
    """
    创建发布任务（支持多平台）。

    为每个 platform 创建一个独立任务。
    """
    queue = _get_queue(request)
    task_ids: List[str] = []

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
            "platform_options": target.platform_options or {},
            "scheduled_at": body.scheduled_at,
        }
        task_id = await queue.enqueue(task_data)
        task_ids.append(task_id)

    return {"success": True, "task_ids": task_ids}


@router.get("/tasks", dependencies=[Depends(require_auth)])
async def list_tasks(
    request: Request,
    platform: Optional[str] = None,
    status: Optional[str] = None,
    account_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """获取任务列表（带过滤 + 分页）。"""
    db = _get_db(request)
    # get_publish_tasks_v2 supports platform/status/account_id/limit
    tasks = db.get_publish_tasks_v2(
        platform=platform,
        status=status,
        account_id=account_id,
        limit=limit + offset,
    )
    # Manual offset slicing (DB layer doesn't support offset natively)
    paginated = tasks[offset: offset + limit]
    return {"success": True, "tasks": paginated, "total": len(tasks)}


@router.get("/tasks/{task_id}", dependencies=[Depends(require_auth)])
async def get_task_detail(task_id: str, request: Request):
    """获取单个任务详情。"""
    db = _get_db(request)
    task = db.get_publish_task_v2(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"success": True, "task": task}


@router.post("/tasks/{task_id}/retry", dependencies=[Depends(require_auth)])
async def retry_task(task_id: str, request: Request):
    """重试失败的任务。"""
    queue = _get_queue(request)
    ok = await queue.retry_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail="任务不存在或状态不允许重试")
    return {"success": True, "message": "任务已重新入队"}


@router.delete("/tasks/{task_id}", dependencies=[Depends(require_auth)])
async def cancel_task(task_id: str, request: Request):
    """取消/删除任务。"""
    queue = _get_queue(request)
    ok = await queue.cancel_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail="任务不存在或状态不允许取消")
    return {"success": True, "message": "任务已取消"}


@router.get("/stats", dependencies=[Depends(require_auth)])
async def get_stats(request: Request):
    """获取发布统计（按状态分组计数）。"""
    db = _get_db(request)
    all_tasks = db.get_publish_tasks_v2(limit=10000)
    stats: Dict[str, int] = {}
    for task in all_tasks:
        s = task.get("status", "unknown")
        stats[s] = stats.get(s, 0) + 1
    stats["total"] = len(all_tasks)
    return {"success": True, "stats": stats}
