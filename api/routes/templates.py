"""
发布模板 API 路由。

提供：
- GET    /api/templates                        模板列表
- POST   /api/templates                        创建模板
- GET    /api/templates/{template_id}          模板详情
- PUT    /api/templates/{template_id}          更新模板
- DELETE /api/templates/{template_id}          删除模板
- POST   /api/templates/{template_id}/apply    应用模板生成任务规格
"""

import json
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.auth import require_auth
from core.database import Database
from platform_services.templates import PublishTemplateService

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_db: Optional[Database] = None


def _get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


def _get_service() -> PublishTemplateService:
    return PublishTemplateService(_get_db())


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class CreateTemplateRequest(BaseModel):
    name: str
    platforms: List[str] = Field(default_factory=list)
    title_template: str = ""
    description_template: str = ""
    tags: List[str] = Field(default_factory=list)
    platform_options: dict = Field(default_factory=dict)
    user_id: str = ""


class UpdateTemplateRequest(BaseModel):
    name: Optional[str] = None
    platforms: Optional[List[str]] = None
    title_template: Optional[str] = None
    description_template: Optional[str] = None
    tags: Optional[List[str]] = None
    platform_options: Optional[dict] = None


class ApplyTemplateRequest(BaseModel):
    video_path: str
    title_vars: dict = Field(default_factory=dict)
    desc_vars: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_templates(user_id: str = ""):
    """列出所有发布模板（可选按 user_id 过滤）。"""
    service = _get_service()
    templates = service.list_templates(user_id=user_id)
    return {"success": True, "data": templates}


@router.post("")
async def create_template(body: CreateTemplateRequest):
    """创建发布模板。"""
    if not body.name:
        return JSONResponse(
            status_code=400,
            content={"success": False, "detail": "模板名称不能为空"},
        )
    service = _get_service()
    result = service.create_template(
        user_id=body.user_id,
        name=body.name,
        platforms=body.platforms,
        title_template=body.title_template,
        description_template=body.description_template,
        tags=body.tags,
        platform_options=body.platform_options,
    )
    return {"success": True, "data": result}


@router.get("/{template_id}")
async def get_template(template_id: str):
    """获取单个模板详情。"""
    service = _get_service()
    template = service.get_template(template_id)
    if not template:
        return JSONResponse(
            status_code=404,
            content={"success": False, "detail": "模板不存在"},
        )
    return {"success": True, "data": template}


@router.put("/{template_id}")
async def update_template(template_id: str, body: UpdateTemplateRequest):
    """更新模板字段。"""
    service = _get_service()
    # Check template exists first
    existing = service.get_template(template_id)
    if not existing:
        return JSONResponse(
            status_code=404,
            content={"success": False, "detail": "模板不存在"},
        )

    update_fields = {}
    if body.name is not None:
        update_fields["name"] = body.name
    if body.platforms is not None:
        update_fields["platforms"] = json.dumps(body.platforms)
    if body.title_template is not None:
        update_fields["title_template"] = body.title_template
    if body.description_template is not None:
        update_fields["description_template"] = body.description_template
    if body.tags is not None:
        update_fields["tags"] = json.dumps(body.tags)
    if body.platform_options is not None:
        update_fields["platform_options"] = json.dumps(body.platform_options)

    if not update_fields:
        return {"success": True, "message": "无需更新"}

    service.update_template(template_id, **update_fields)
    return {"success": True, "message": "模板已更新"}


@router.delete("/{template_id}")
async def delete_template(template_id: str):
    """删除模板。"""
    service = _get_service()
    ok = service.delete_template(template_id)
    if not ok:
        return JSONResponse(
            status_code=404,
            content={"success": False, "detail": "模板不存在"},
        )
    return {"success": True, "message": "模板已删除"}


@router.post("/{template_id}/apply")
async def apply_template(template_id: str, body: ApplyTemplateRequest):
    """应用模板生成发布任务规格。"""
    service = _get_service()
    tasks = service.apply_template(
        template_id=template_id,
        video_path=body.video_path,
        title_vars=body.title_vars,
        desc_vars=body.desc_vars,
    )
    if not tasks:
        return JSONResponse(
            status_code=404,
            content={"success": False, "detail": "模板不存在或无平台配置"},
        )
    return {"success": True, "data": tasks}
