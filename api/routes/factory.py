"""
加工管线路由 - 触发二次创作
"""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from core.task import Task, TaskState, TaskStore
from factory.pipeline import FactoryPipeline

logger = logging.getLogger(__name__)
router = APIRouter()

_task_store: Optional[TaskStore] = None
_factory: Optional[FactoryPipeline] = None


def get_task_store() -> TaskStore:
    global _task_store
    if _task_store is None:
        _task_store = TaskStore()
    return _task_store


def get_factory() -> FactoryPipeline:
    global _factory
    if _factory is None:
        _factory = FactoryPipeline(task_store=get_task_store())
    return _factory


class RunFactoryRequest(BaseModel):
    """运行加工管线请求"""
    task_id: str = Field(..., description="任务ID")


async def _run_factory(task_id: str):
    """后台运行加工管线"""
    store = get_task_store()
    factory = get_factory()

    task = store.get(task_id)
    if task:
        await factory.run(task)


@router.post("/run")
async def run_factory(request: RunFactoryRequest, background_tasks: BackgroundTasks):
    """
    运行加工管线（后台执行）

    触发: 长视频加工 + 短视频切片 + 封面 + 元数据 + 图文
    """
    store = get_task_store()
    task = store.get(request.task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {request.task_id}")

    if task.state != TaskState.QC_PASSED.value:
        raise HTTPException(
            status_code=400,
            detail=f"任务未通过质检，不能运行加工管线: {task.state}"
        )

    background_tasks.add_task(_run_factory, request.task_id)

    return {
        "message": "加工管线已启动",
        "task_id": task.task_id,
    }


@router.get("/status/{task_id}")
async def get_factory_status(task_id: str):
    """获取加工状态"""
    store = get_task_store()
    task = store.get(task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    return {
        "task_id": task.task_id,
        "state": task.state,
        "progress": task.progress,
        "products_count": len(task.products),
        "products": task.products,
    }
