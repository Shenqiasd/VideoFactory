"""
生产管线路由 - 触发翻译配音流程
"""
import asyncio
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field, field_validator

from core.project_naming import resolve_project_titles
from core.task import Task, TaskState, TaskStore, normalize_creation_config
from core.runtime_settings import get_subtitle_style_defaults
from core.subtitle_style import normalize_subtitle_style
from production.pipeline import ProductionPipeline

logger = logging.getLogger(__name__)
router = APIRouter()

# 全局实例
_task_store: Optional[TaskStore] = None
_pipeline: Optional[ProductionPipeline] = None


def get_task_store() -> TaskStore:
    global _task_store
    if _task_store is None:
        _task_store = TaskStore()
    return _task_store


def get_pipeline() -> ProductionPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = ProductionPipeline(task_store=get_task_store())
    return _pipeline


# ========== 请求模型 ==========

class RunProductionRequest(BaseModel):
    """运行生产管线请求"""
    task_id: str = Field(..., description="任务ID")


_ALLOWED_LANGS = {
    "en", "zh_cn", "zh", "ja", "ko", "fr", "de", "es", "pt", "ru", "ar",
    "en-us", "en-gb", "zh-cn", "zh-tw",
}


class SubmitAndRunRequest(BaseModel):
    """创建并运行请求"""
    source_url: str = Field(..., description="视频URL")
    source_title: str = Field("", description="视频标题")
    source_lang: str = Field("en", description="源语言")
    target_lang: str = Field("zh_cn", description="目标语言")
    enable_tts: bool = Field(True, description="启用配音")
    embed_subtitle_type: str = Field("horizontal", description="字幕类型")
    subtitle_style: Optional[dict] = Field(None, description="字幕样式")
    creation_config: Optional[dict] = Field(None, description="创作配置")

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("source_url 不能为空")
        if v.startswith(("http://", "https://", "/", "~")):
            return v
        raise ValueError("source_url 必须是 http/https URL 或绝对路径")

    @field_validator("source_lang", "target_lang")
    @classmethod
    def validate_lang(cls, v):
        normalized = v.lower().replace("-", "_")
        if normalized not in {x.replace("-", "_") for x in _ALLOWED_LANGS}:
            raise ValueError(f"不支持的语言代码: {v}")
        return v

    @field_validator("embed_subtitle_type")
    @classmethod
    def validate_embed_type(cls, v):
        if v not in {"horizontal", "vertical", "none"}:
            raise ValueError("embed_subtitle_type 必须是 horizontal/vertical/none 之一")
        return v


# ========== 后台任务 ==========

async def _run_production(task_id: str):
    """后台运行生产管线"""
    store = get_task_store()
    pipeline = get_pipeline()

    task = store.get(task_id)
    if task:
        await pipeline.run(task)


# ========== 路由 ==========

@router.post("/run")
async def run_production(request: RunProductionRequest, background_tasks: BackgroundTasks):
    """
    运行生产管线（后台执行）

    触发: 下载 → 自管翻译/配音 → 质检
    """
    store = get_task_store()
    task = store.get(request.task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {request.task_id}")

    if task.state not in [TaskState.QUEUED.value, TaskState.QC_FAILED.value]:
        raise HTTPException(
            status_code=400,
            detail=f"任务状态不允许运行生产管线: {task.state}"
        )

    background_tasks.add_task(_run_production, request.task_id)

    return {
        "message": f"生产管线已启动",
        "task_id": task.task_id,
        "state": task.state,
    }


@router.post("/submit-and-run")
async def submit_and_run(request: SubmitAndRunRequest, background_tasks: BackgroundTasks):
    """
    一键提交并运行
    创建任务 + 启动生产管线
    """
    store = get_task_store()
    resolved_titles = await resolve_project_titles(
        source_url=request.source_url,
        source_title=request.source_title,
        source_lang=request.source_lang,
        target_lang=request.target_lang,
    )

    # 创建任务
    task = store.create(
        source_url=request.source_url,
        source_title=resolved_titles.source_title,
        translated_title=resolved_titles.project_name,
        source_lang=request.source_lang,
        target_lang=request.target_lang,
        enable_tts=request.enable_tts,
        embed_subtitle_type=request.embed_subtitle_type,
        subtitle_style=normalize_subtitle_style(
            request.subtitle_style,
            defaults=get_subtitle_style_defaults(),
        ),
        creation_config=normalize_creation_config(request.creation_config, enable_short_clips=True),
    )

    # 后台运行
    background_tasks.add_task(_run_production, task.task_id)

    return {
        "message": "任务已创建并启动生产管线",
        "task_id": task.task_id,
    }


@router.get("/status/{task_id}")
async def get_production_status(task_id: str):
    """获取生产状态"""
    store = get_task_store()
    task = store.get(task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    return {
        "task_id": task.task_id,
        "state": task.state,
        "progress": task.progress,
        "translation_task_id": task.translation_task_id,
        "translation_progress": task.translation_progress,
        "translated_title": task.translated_title,
        "qc_score": task.qc_score,
        "qc_details": task.qc_details,
        "global_review_report": getattr(task, "global_review_report", {}) or {},
        "error_message": task.error_message,
    }
