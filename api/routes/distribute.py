"""
分发路由 - 管理多平台发布
"""
import logging
import time
from typing import Optional, List
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from core.task import TaskState, TaskStore
from distribute.scheduler import PublishScheduler

logger = logging.getLogger(__name__)
router = APIRouter()

_task_store: Optional[TaskStore] = None
_scheduler: Optional[PublishScheduler] = None


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


class PublishRequest(BaseModel):
    """发布请求"""
    task_id: str = Field(..., description="任务ID")
    platforms: List[str] = Field(default=["bilibili", "douyin", "xiaohongshu", "youtube"])
    publish_accounts: dict[str, str] = Field(default_factory=dict, description="平台到账号ID的绑定")
    mode: str = Field("immediate", description="发布模式: immediate/staggered/scheduled")
    interval_minutes: int = Field(30, description="错峰发布间隔（分钟）")
    force_republish: bool = Field(False, description="是否忽略幂等键强制重新调度")


def _validate_publish_accounts(account_map: dict[str, str], platforms: List[str]) -> dict[str, str]:
    if not account_map:
        return {}

    from core.database import Database

    db = Database()
    normalized: dict[str, str] = {}
    for platform, account_id in account_map.items():
        if not account_id or platform not in platforms:
            continue
        account = db.get_account(account_id)
        if not account:
            raise HTTPException(
                status_code=400,
                detail={"code": "ACCOUNT_NOT_FOUND", "message": f"账号不存在: {account_id}"},
            )
        if account.get("platform") != platform:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "ACCOUNT_PLATFORM_MISMATCH",
                    "message": f"账号 {account.get('name', account_id)} 不属于平台 {platform}",
                },
            )
        normalized[platform] = account_id
    return normalized


class ReplayRequest(BaseModel):
    """重放失败发布任务"""
    task_id: str
    job_id: str = ""
    platform: str = ""
    product_type: str = ""


class ManualReviewRequest(BaseModel):
    """手动发布确认"""
    task_id: str
    job_id: str
    publish_url: str = ""
    error: str = ""
    note: str = ""


async def _run_due_jobs(scheduler: PublishScheduler):
    due_jobs = [j for j in scheduler._queue if j.status == "pending" and j.is_due()]
    for job in due_jobs:
        await scheduler._execute_job(job)


@router.post("/publish")
async def publish(request: PublishRequest, background_tasks: BackgroundTasks):
    """
    发布任务到多平台
    """
    store = get_task_store()
    task = store.get(request.task_id)

    if not task:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "message": f"任务不存在: {request.task_id}"})

    if task.state != TaskState.READY_TO_PUBLISH.value:
        raise HTTPException(
            status_code=400,
            detail={"code": "TASK_NOT_READY", "message": f"任务未就绪，当前状态: {task.state}"},
        )

    if not task.products:
        raise HTTPException(status_code=400, detail={"code": "NO_PRODUCTS", "message": "任务没有产出物"})

    publish_accounts = _validate_publish_accounts(request.publish_accounts, request.platforms)
    if publish_accounts:
        task.publish_accounts.update(publish_accounts)

    scheduler = get_scheduler()
    if request.mode == "immediate":
        stats = scheduler.schedule_immediate(task, request.platforms, force=request.force_republish)
    elif request.mode == "staggered":
        stats = scheduler.schedule_staggered(
            task,
            request.platforms,
            interval_minutes=request.interval_minutes,
            force=request.force_republish,
        )
    else:
        stats = scheduler.schedule_immediate(task, request.platforms, force=request.force_republish)

    task.transition(TaskState.PUBLISHING)
    store.update(task)

    # 将队列中已到期任务放入后台执行
    background_tasks.add_task(
        _run_due_jobs,
        scheduler,
    )

    return {
        "message": f"发布已调度 ({request.mode})",
        "task_id": task.task_id,
        "platforms": request.platforms,
        "publish_accounts": publish_accounts,
        "products_count": len(task.products),
        "added_jobs": stats["added"],
        "skipped_jobs": stats["skipped"],
        "force_republish": request.force_republish,
    }


@router.get("/queue")
async def get_publish_queue():
    """获取发布队列状态"""
    scheduler = get_scheduler()
    status = scheduler.get_queue_status()

    return {
        "queue_status": status,
        "total_jobs": sum(status.values()),
        "pending_jobs": [j.to_dict() for j in scheduler._queue if j.status in ("pending", "publishing")],
    }


@router.get("/status/{task_id}")
async def get_publish_status(task_id: str):
    """获取任务的发布状态"""
    store = get_task_store()
    task = store.get(task_id)

    if not task:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "message": f"任务不存在: {task_id}"})

    scheduler = get_scheduler()
    task_jobs = [j.to_dict() for j in scheduler._queue if j.task_id == task_id]

    return {
        "task_id": task_id,
        "state": task.state,
        "publish_jobs": task_jobs,
    }


@router.get("/events/{task_id}")
async def get_publish_events(task_id: str, limit: int = 100):
    from core.database import Database

    db = Database()
    events = db.get_publish_job_events(task_id=task_id, limit=max(1, min(limit, 200)))
    return {
        "task_id": task_id,
        "count": len(events),
        "events": events,
    }


@router.post("/replay")
async def replay_failed_publish(request: ReplayRequest, background_tasks: BackgroundTasks):
    """重放失败的发布任务"""
    store = get_task_store()
    task = store.get(request.task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "message": f"任务不存在: {request.task_id}"})

    scheduler = get_scheduler()
    replayed = scheduler.replay_failed(
        task_id=request.task_id,
        job_id=request.job_id or None,
        platform=request.platform or None,
        product_type=request.product_type or None,
    )
    if replayed == 0:
        raise HTTPException(
            status_code=404,
            detail={"code": "NO_FAILED_JOBS", "message": "没有可重放的失败发布任务"},
        )

    if task.state == TaskState.PARTIAL_SUCCESS.value:
        task.transition(TaskState.PUBLISHING)
        store.update(task)
    elif task.state != TaskState.PUBLISHING.value:
        if not task.transition(TaskState.PUBLISHING):
            # 允许从已完成/失败状态手动重放
            task.state = TaskState.PUBLISHING.value
            task.updated_at = time.time()
        store.update(task)

    background_tasks.add_task(_run_due_jobs, scheduler)
    return {
        "message": "失败发布任务已重放",
        "task_id": request.task_id,
        "replayed_jobs": replayed,
        "job_id": request.job_id or "all",
        "platform": request.platform or "all",
        "product_type": request.product_type or "all",
    }


@router.get("/stats")
async def get_publish_stats():
    """获取发布统计信息"""
    scheduler = get_scheduler()
    status_count = scheduler.get_queue_status()

    platform_stats = {}
    for job in scheduler._queue:
        if job.platform not in platform_stats:
            platform_stats[job.platform] = {"pending": 0, "publishing": 0, "done": 0, "failed": 0}
        platform_stats[job.platform][job.status] = platform_stats[job.platform].get(job.status, 0) + 1

    return {
        "total": sum(status_count.values()),
        "by_status": status_count,
        "by_platform": platform_stats,
    }


@router.post("/tasks/{task_id}/execute")
async def execute_publish_task(task_id: str, background_tasks: BackgroundTasks):
    """立即执行指定任务的发布作业"""
    scheduler = get_scheduler()
    task_jobs = [j for j in scheduler._queue if j.task_id == task_id and j.status == "pending" and j.is_due()]

    if not task_jobs:
        raise HTTPException(
            status_code=404,
            detail={"code": "NO_PENDING_JOBS", "message": "没有待执行的发布任务"}
        )

    background_tasks.add_task(_run_due_jobs, scheduler)

    return {
        "message": "发布任务已加入执行队列",
        "task_id": task_id,
        "jobs_count": len(task_jobs),
    }


@router.post("/tasks/{task_id}/retry")
async def retry_publish_task(task_id: str, background_tasks: BackgroundTasks):
    """重试指定任务的失败发布作业"""
    scheduler = get_scheduler()
    replayed = scheduler.replay_failed(task_id=task_id)

    if replayed == 0:
        raise HTTPException(
            status_code=404,
            detail={"code": "NO_FAILED_JOBS", "message": "没有失败的发布任务"}
        )

    background_tasks.add_task(_run_due_jobs, scheduler)

    return {
        "message": "失败任务已重置并加入执行队列",
        "task_id": task_id,
        "replayed_jobs": replayed,
    }


class CancelRequest(BaseModel):
    """取消发布任务"""
    task_id: str
    job_id: str = ""
    platform: str = ""


@router.post("/cancel")
async def cancel_publish_job(request: CancelRequest):
    """取消待发布或发布中的任务"""
    scheduler = get_scheduler()
    cancelled = scheduler.cancel(
        task_id=request.task_id,
        job_id=request.job_id or None,
        platform=request.platform or None,
    )

    if cancelled == 0:
        raise HTTPException(status_code=404, detail={"code": "NO_JOBS", "message": "没有可取消的任务"})

    if task := get_task_store().get(request.task_id):
        if task.state == TaskState.PUBLISHING.value:
            await scheduler._check_task_completion(task.task_id)

    return {
        "message": "任务已取消",
        "cancelled": cancelled,
        "job_id": request.job_id or "all",
    }


@router.post("/manual/complete")
async def complete_manual_publish(request: ManualReviewRequest):
    """手动确认发布成功"""
    scheduler = get_scheduler()
    try:
        job = await scheduler.mark_manual_result(
            task_id=request.task_id,
            job_id=request.job_id,
            success=True,
            publish_url=request.publish_url,
            note=request.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "INVALID_MANUAL_JOB", "message": str(exc)}) from exc

    return {
        "message": "手动发布已确认",
        "task_id": request.task_id,
        "job_id": request.job_id,
        "job": job.to_dict(),
    }


@router.post("/manual/fail")
async def fail_manual_publish(request: ManualReviewRequest):
    """手动确认发布失败"""
    scheduler = get_scheduler()
    try:
        job = await scheduler.mark_manual_result(
            task_id=request.task_id,
            job_id=request.job_id,
            success=False,
            error=request.error,
            note=request.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "INVALID_MANUAL_JOB", "message": str(exc)}) from exc

    return {
        "message": "手动发布失败已记录",
        "task_id": request.task_id,
        "job_id": request.job_id,
        "job": job.to_dict(),
    }
