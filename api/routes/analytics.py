"""
数据分析 API 路由 — 内容统计 + Token 健康检查。

提供：
- GET    /api/analytics/summary          跨平台数据汇总
- GET    /api/analytics/tasks/{task_id}  单任务历史分析
- POST   /api/analytics/sync             手动触发数据同步
- GET    /api/analytics/top              热门内容排行
- GET    /api/analytics/token-health     Token 过期检测
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from api.auth import require_auth
from core.database import Database
from platform_services.token_manager import TokenManager
from platform_services.registry import PlatformRegistry
from platform_services.analytics import AnalyticsService

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])

# ---------------------------------------------------------------------------
# Module-level singletons (injected by server.py at startup)
# ---------------------------------------------------------------------------

_db: Optional[Database] = None
_token_manager: Optional[TokenManager] = None
_analytics_service: Optional[AnalyticsService] = None


def init_analytics(db: Database, token_manager: TokenManager) -> None:
    """Called by server.py to inject dependencies."""
    global _db, _token_manager, _analytics_service
    _db = db
    _token_manager = token_manager
    _analytics_service = AnalyticsService(
        db=db,
        token_manager=token_manager,
        registry=PlatformRegistry,
    )


def _get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


def _get_analytics() -> AnalyticsService:
    global _analytics_service
    if _analytics_service is None:
        db = _get_db()
        from platform_services.token_manager import TokenManager as TM
        tm = TM(db)
        _analytics_service = AnalyticsService(
            db=db, token_manager=tm, registry=PlatformRegistry,
        )
    return _analytics_service


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/summary")
async def analytics_summary():
    """跨平台数据汇总（按平台分组的总 views/likes/comments/shares）。"""
    svc = _get_analytics()
    summary = svc.get_analytics_summary()
    return {"success": True, "data": summary}


@router.get("/tasks/{task_id}")
async def task_analytics(task_id: str):
    """获取单个发布任务的历史分析数据。"""
    svc = _get_analytics()
    records = svc.get_task_analytics(task_id)
    return {"success": True, "data": records}


@router.post("/sync")
async def sync_analytics():
    """手动触发全量数据同步。"""
    svc = _get_analytics()
    try:
        results = await svc.sync_all_stats()
        return {"success": True, "data": results}
    except Exception as exc:
        logger.exception("数据同步失败")
        return JSONResponse(
            status_code=500,
            content={"success": False, "detail": str(exc)},
        )


@router.get("/top")
async def top_content(limit: int = 10):
    """热门内容排行（按播放量排序）。"""
    svc = _get_analytics()
    items = svc.get_top_content(limit=limit)
    return {"success": True, "data": items}


@router.get("/token-health")
async def token_health():
    """Token 过期检测。"""
    global _token_manager
    if _token_manager is None:
        db = _get_db()
        _token_manager = TokenManager(db)
    alerts = await _token_manager.check_all_token_health()
    return {"success": True, "data": alerts}
