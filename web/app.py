"""
发布任务 CRUD API
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from distribute.scheduler import PublishScheduler
from core.task import TaskStore
from core.database import Database

router = APIRouter()

_task_store: Optional[TaskStore] = None
_scheduler: Optional[PublishScheduler] = None
_database: Optional[Database] = None


def get_task_store() -> TaskStore:
    global _task_store
    if _task_store is None:
        _task_store = TaskStore()
    return _task_store


def get_scheduler() -> PublishScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = PublishScheduler(task_store=get_task_store())
    return _scheduler


def get_database() -> Database:
    global _database
    if _database is None:
        _database = Database()
    return _database


class CreateTaskRequest(BaseModel):
    task_id: str
    platform: str
    scheduled_time: float
    product: dict


@router.post("/api/publish/tasks")
async def create_publish_task(request: CreateTaskRequest):
    """创建发布任务"""
    scheduler = get_scheduler()
    job = scheduler._create_job(
        task_id=request.task_id,
        platform=request.platform,
        scheduled_time=request.scheduled_time,
        product=request.product
    )
    scheduler._queue.append(job)
    scheduler._save_queue()

    return {"message": "任务已创建", "job": job.to_dict()}


@router.get("/api/publish/tasks")
async def get_publish_tasks(status: Optional[str] = None):
    """获取发布任务列表"""
    scheduler = get_scheduler()
    jobs = scheduler._queue

    if status:
        jobs = [j for j in jobs if j.status == status]

    return {"tasks": [j.to_dict() for j in jobs], "total": len(jobs)}


@router.delete("/api/publish/tasks/{task_id}")
async def delete_publish_task(task_id: str):
    """删除发布任务"""
    scheduler = get_scheduler()
    initial_count = len(scheduler._queue)
    scheduler._queue = [j for j in scheduler._queue if j.task_id != task_id]
    deleted = initial_count - len(scheduler._queue)

    if deleted == 0:
        raise HTTPException(status_code=404, detail="任务不存在")

    scheduler._save_queue()
    return {"message": f"已删除 {deleted} 个任务", "deleted_count": deleted}


@router.post("/api/publish/accounts/{id}/test")
async def test_account(id: str):
    """测试账号连接"""
    db = get_database()
    account = db.get_account(id)

    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")

    cookie_exists = os.path.exists(account['cookie_path'])

    db.update_account_test_time(id, datetime.now())

    return {
        "success": cookie_exists,
        "account_id": id,
        "platform": account['platform'],
        "cookie_exists": cookie_exists,
        "tested_at": datetime.now().isoformat()
    }
