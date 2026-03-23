"""
FastAPI 主服务
OpenClaw通过这个API来操控video-factory
"""
import sys
import os
import logging
from contextlib import asynccontextmanager

# 确保src在Python路径中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel

from core.runtime import read_worker_heartbeat
from api.auth import (
    _AuthRedirect,
    _COOKIE_NAME,
    _SESSION_MAX_AGE,
    auth_enabled,
    create_session_token,
    create_user,
    get_user_by_username,
    registration_allowed,
    verify_password,
    verify_session_token,
    _extract_session,
)
from api.routes.tasks import router as tasks_router
from api.routes.production import router as production_router
from api.routes.factory import router as factory_router
from api.routes.distribute import router as distribute_router
from api.routes.system import router as system_router
from api.routes.pages import router as pages_router
from api.routes.publish import router as publish_router
from api.routes.storage import router as storage_router
from api.routes.monitor import router as monitor_router
from api.routes.oauth import router as oauth_router
from api.routes.publish_v2 import router as publish_v2_router, set_publish_queue
from api.routes.templates import router as templates_router
from api.routes.analytics import router as analytics_router, init_analytics
from core.scheduler import StorageCleanupScheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动
    logger.info("🚀 video-factory API 启动中...")

    # 初始化全局资源
    from core.config import Config
    config = Config()
    logger.info(f"📋 配置加载完成")
    cleanup_scheduler = StorageCleanupScheduler()
    cleanup_scheduler.start()
    app.state.storage_cleanup_scheduler = cleanup_scheduler

    # 注册平台服务（YouTube / Bilibili）
    from platform_services.registry import PlatformRegistry

    callback_base = config.get("oauth", "callback_base_url", default="http://localhost:9000")

    yt_client_id = config.get("oauth", "youtube", "client_id", default="")
    yt_client_secret = config.get("oauth", "youtube", "client_secret", default="")
    if yt_client_id and yt_client_secret:
        from platform_services.youtube import YouTubeService
        yt_service = YouTubeService(
            client_id=yt_client_id,
            client_secret=yt_client_secret,
            redirect_uri=f"{callback_base}/api/oauth/callback/youtube",
        )
        PlatformRegistry.register(yt_service)
        logger.info("YouTube 平台服务已注册")

    bili_client_id = config.get("oauth", "bilibili", "client_id", default="")
    bili_client_secret = config.get("oauth", "bilibili", "client_secret", default="")
    if bili_client_id and bili_client_secret:
        from platform_services.bilibili import BilibiliService
        bili_service = BilibiliService(
            client_id=bili_client_id,
            client_secret=bili_client_secret,
            redirect_uri=f"{callback_base}/api/oauth/callback/bilibili",
        )
        PlatformRegistry.register(bili_service)
        logger.info("Bilibili 平台服务已注册")

    # 初始化发布队列
    from core.database import Database
    from platform_services.token_manager import TokenManager
    from platform_services.publish_queue import PublishQueue

    db = Database()
    token_manager = TokenManager(db)
    publish_queue = PublishQueue(db=db, token_manager=token_manager)
    set_publish_queue(publish_queue)
    init_analytics(db, token_manager)
    await publish_queue.start()
    app.state.publish_queue = publish_queue
    logger.info("📤 发布队列已启动")

    yield

    # 关闭发布队列
    pq = getattr(app.state, "publish_queue", None)
    if pq:
        await pq.stop()

    # 关闭
    scheduler = getattr(app.state, "storage_cleanup_scheduler", None)
    if scheduler:
        scheduler.shutdown()
    logger.info("🛑 video-factory API 关闭中...")


app = FastAPI(
    title="Video Factory API",
    description="自动化视频翻译、配音、二次创作和多平台分发系统",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS（允许OpenClaw等客户端调用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth redirect exception handler
@app.exception_handler(_AuthRedirect)
async def _handle_auth_redirect(request: Request, exc: _AuthRedirect):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=exc.location, status_code=302)


# 挂载静态文件
BASE_DIR = Path(__file__).resolve().parents[1]
static_dir = BASE_DIR / "web" / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# ---------------------------------------------------------------------------
# Auth routes (register / login / logout / status)
# ---------------------------------------------------------------------------


class _LoginRequest(BaseModel):
    username: str
    password: str


class _RegisterRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/register")
async def auth_register(body: _RegisterRequest):
    if not registration_allowed():
        return JSONResponse(
            status_code=403,
            content={"success": False, "detail": "注册已关闭"},
        )
    username = (body.username or "").strip()
    password = body.password or ""
    if not username or len(username) < 2:
        return JSONResponse(
            status_code=400,
            content={"success": False, "detail": "用户名至少需要 2 个字符"},
        )
    if len(password) < 6:
        return JSONResponse(
            status_code=400,
            content={"success": False, "detail": "密码至少需要 6 个字符"},
        )
    try:
        create_user(username, password)
    except ValueError as e:
        return JSONResponse(
            status_code=409,
            content={"success": False, "detail": str(e)},
        )
    token = create_session_token(username)
    response = JSONResponse(content={"success": True, "message": "注册成功"})
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=_SESSION_MAX_AGE,
        path="/",
    )
    return response


@app.post("/api/auth/login")
async def auth_login(body: _LoginRequest):
    if not auth_enabled():
        return JSONResponse(
            status_code=401,
            content={"success": False, "detail": "请先注册账户"},
        )
    username = (body.username or "").strip()
    user = get_user_by_username(username)
    if not user or not verify_password(body.password, user["password_hash"]):
        return JSONResponse(
            status_code=401,
            content={"success": False, "detail": "用户名或密码错误"},
        )
    token = create_session_token(username)
    response = JSONResponse(content={"success": True, "message": "登录成功"})
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=_SESSION_MAX_AGE,
        path="/",
    )
    return response


@app.post("/api/auth/logout")
async def auth_logout():
    response = JSONResponse(content={"success": True, "message": "已退出"})
    response.delete_cookie(key=_COOKIE_NAME, path="/")
    return response


@app.get("/api/auth/status")
async def auth_status(request: Request):
    enabled = auth_enabled()
    if not enabled:
        return {
            "auth_enabled": False,
            "authenticated": False,
            "registration_allowed": registration_allowed(),
        }
    token = _extract_session(request)
    username = verify_session_token(token) if token else None
    return {
        "auth_enabled": True,
        "authenticated": username is not None,
        "username": username,
        "registration_allowed": registration_allowed(),
    }


# 注册路由 - 页面路由放在最前面（无prefix）
app.include_router(pages_router, tags=["前端页面"])

# API路由
app.include_router(tasks_router, prefix="/api/tasks", tags=["任务管理"])
app.include_router(production_router, prefix="/api/production", tags=["生产管线"])
app.include_router(factory_router, prefix="/api/factory", tags=["加工管线"])
app.include_router(distribute_router, prefix="/api/distribute", tags=["分发管线"])
app.include_router(publish_router, prefix="/api/publish", tags=["发布账号"])
app.include_router(system_router, prefix="/api/system", tags=["系统"])
app.include_router(storage_router, prefix="/api", tags=["存储管理"])
app.include_router(monitor_router, prefix="/api/monitor", tags=["频道监控"])
app.include_router(oauth_router, prefix="/api/oauth", tags=["平台OAuth"])
app.include_router(publish_v2_router, prefix="/api/publish/v2", tags=["多平台发布V2"])
app.include_router(templates_router, prefix="/api/templates", tags=["发布模板"])
app.include_router(analytics_router, prefix="/api/analytics", tags=["数据分析"])


@app.get("/api")
async def api_root():
    """API信息"""
    return {
        "service": "video-factory",
        "version": "0.1.0",
        "status": "running",
    }


@app.get("/api/health")
async def health():
    """健康检查"""
    heartbeat = read_worker_heartbeat(max_age_seconds=90)

    # Add queue stats
    queue_stats = {}
    pq = getattr(app.state, "publish_queue", None)
    if pq:
        queue_stats = pq.db.count_publish_tasks_v2_by_status()

    return {
        "status": "healthy",
        "service": "video-factory",
        "worker": {
            "alive": heartbeat["alive"],
            "last_heartbeat": heartbeat["timestamp"],
            "pid": heartbeat["pid"],
            "reason": heartbeat["reason"],
        },
        "queue": queue_stats,
    }


# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


if __name__ == "__main__":
    import uvicorn
    api_host = os.environ.get("VF_API_HOST", os.environ.get("HOST", "0.0.0.0"))
    api_port = int(os.environ.get("VF_API_PORT", os.environ.get("PORT", "9000")))
    uvicorn.run(
        "api.server:app",
        host=api_host,
        port=api_port,
        reload=True,
        log_level="info",
    )
