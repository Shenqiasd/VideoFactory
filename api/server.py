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
from api.routes.storage import router as storage_router
from api.routes.monitor import router as monitor_router
from api.routes.oauth import router as oauth_router
from api.routes.publish_v2 import router as publish_v2_router, set_publish_queue
from api.routes.templates import router as templates_router
from api.routes.analytics import router as analytics_router, init_analytics
from core.scheduler import StorageCleanupScheduler

logger = logging.getLogger(__name__)


def register_platform_services() -> int:
    """
    根据 settings.yaml 中的 OAuth 配置注册平台服务。

    可在启动时调用，也可在保存 OAuth 设置后再次调用以热加载。
    返回成功注册的平台数量。
    """
    from core.config import Config
    from platform_services.registry import PlatformRegistry

    Config.reset()
    config = Config()

    PlatformRegistry.clear()

    callback_base = config.get("oauth", "callback_base_url", default="http://localhost:9000")

    def _redirect(platform: str) -> str:
        return f"{callback_base}/api/oauth/callback/{platform}"

    count = 0

    # YouTube
    yt_id = config.get("oauth", "youtube", "client_id", default="")
    yt_sec = config.get("oauth", "youtube", "client_secret", default="")
    if yt_id and yt_sec:
        from platform_services.youtube import YouTubeService
        PlatformRegistry.register(YouTubeService(
            client_id=yt_id, client_secret=yt_sec, redirect_uri=_redirect("youtube"),
        ))
        count += 1

    # Bilibili
    bili_id = config.get("oauth", "bilibili", "client_id", default="")
    bili_sec = config.get("oauth", "bilibili", "client_secret", default="")
    if bili_id and bili_sec:
        from platform_services.bilibili import BilibiliService
        PlatformRegistry.register(BilibiliService(
            client_id=bili_id, client_secret=bili_sec, redirect_uri=_redirect("bilibili"),
        ))
        count += 1

    # TikTok
    tt_id = config.get("oauth", "tiktok", "client_id", default="")
    tt_sec = config.get("oauth", "tiktok", "client_secret", default="")
    if tt_id and tt_sec:
        from platform_services.tiktok import TikTokService
        PlatformRegistry.register(TikTokService(
            client_id=tt_id, client_secret=tt_sec, redirect_uri=_redirect("tiktok"),
        ))
        count += 1

    # 抖音 (Douyin)
    dy_id = config.get("oauth", "douyin", "client_id", default="")
    dy_sec = config.get("oauth", "douyin", "client_secret", default="")
    if dy_id and dy_sec:
        from platform_services.douyin import DouyinService
        PlatformRegistry.register(DouyinService(
            client_key=dy_id, client_secret=dy_sec, redirect_uri=_redirect("douyin"),
        ))
        count += 1

    # Facebook
    fb_id = config.get("oauth", "facebook", "app_id", default="")
    fb_sec = config.get("oauth", "facebook", "app_secret", default="")
    if fb_id and fb_sec:
        from platform_services.facebook import FacebookService
        PlatformRegistry.register(FacebookService(
            client_id=fb_id, client_secret=fb_sec, redirect_uri=_redirect("facebook"),
        ))
        count += 1

    # Instagram
    ig_id = config.get("oauth", "instagram", "app_id", default="")
    ig_sec = config.get("oauth", "instagram", "app_secret", default="")
    if ig_id and ig_sec:
        from platform_services.instagram import InstagramService
        PlatformRegistry.register(InstagramService(
            client_id=ig_id, client_secret=ig_sec, redirect_uri=_redirect("instagram"),
        ))
        count += 1

    # Twitter/X
    tw_id = config.get("oauth", "twitter", "client_id", default="")
    tw_sec = config.get("oauth", "twitter", "client_secret", default="")
    if tw_id and tw_sec:
        from platform_services.twitter import TwitterService
        PlatformRegistry.register(TwitterService(
            client_id=tw_id, client_secret=tw_sec, redirect_uri=_redirect("twitter"),
        ))
        count += 1

    # Pinterest
    pin_id = config.get("oauth", "pinterest", "client_id", default="")
    pin_sec = config.get("oauth", "pinterest", "client_secret", default="")
    if pin_id and pin_sec:
        from platform_services.pinterest import PinterestService
        PlatformRegistry.register(PinterestService(
            client_id=pin_id, client_secret=pin_sec, redirect_uri=_redirect("pinterest"),
        ))
        count += 1

    # LinkedIn
    li_id = config.get("oauth", "linkedin", "client_id", default="")
    li_sec = config.get("oauth", "linkedin", "client_secret", default="")
    if li_id and li_sec:
        from platform_services.linkedin import LinkedInService
        PlatformRegistry.register(LinkedInService(
            client_id=li_id, client_secret=li_sec, redirect_uri=_redirect("linkedin"),
        ))
        count += 1

    # 快手 (Kwai)
    kwai_id = config.get("oauth", "kwai", "client_id", default="")
    kwai_sec = config.get("oauth", "kwai", "client_secret", default="")
    if kwai_id and kwai_sec:
        from platform_services.kwai import KwaiService
        PlatformRegistry.register(KwaiService(
            client_id=kwai_id, client_secret=kwai_sec, redirect_uri=_redirect("kwai"),
        ))
        count += 1

    # 小红书 (Xiaohongshu)
    xhs_id = config.get("oauth", "xiaohongshu", "client_id", default="")
    xhs_sec = config.get("oauth", "xiaohongshu", "client_secret", default="")
    if xhs_id and xhs_sec:
        from platform_services.xiaohongshu import XiaohongshuService
        PlatformRegistry.register(XiaohongshuService(
            client_id=xhs_id, client_secret=xhs_sec, redirect_uri=_redirect("xiaohongshu"),
        ))
        count += 1

    # 微信视频号 (Weixin SPH / Channels)
    wsph_id = config.get("oauth", "weixin_sph", "app_id", default="")
    wsph_sec = config.get("oauth", "weixin_sph", "app_secret", default="")
    if wsph_id and wsph_sec:
        from platform_services.weixin_channels import WeixinChannelsService
        PlatformRegistry.register(WeixinChannelsService(
            app_id=wsph_id, app_secret=wsph_sec, redirect_uri=_redirect("weixin_sph"),
        ))
        count += 1

    # 微信公众号 (Weixin GZH / Official Account)
    wgzh_id = config.get("oauth", "weixin_gzh", "app_id", default="")
    wgzh_sec = config.get("oauth", "weixin_gzh", "app_secret", default="")
    if wgzh_id and wgzh_sec:
        from platform_services.weixin_gzh import WeixinGzhService
        PlatformRegistry.register(WeixinGzhService(
            app_id=wgzh_id, app_secret=wgzh_sec, redirect_uri=_redirect("weixin_gzh"),
        ))
        count += 1

    # Threads (Meta)
    thr_id = config.get("oauth", "threads", "app_id", default="")
    thr_sec = config.get("oauth", "threads", "app_secret", default="")
    if thr_id and thr_sec:
        from platform_services.threads import ThreadsService
        PlatformRegistry.register(ThreadsService(
            client_id=thr_id, client_secret=thr_sec, redirect_uri=_redirect("threads"),
        ))
        count += 1

    logger.info("平台服务注册完成: %d 个平台已注册", count)
    return count


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

    # 注册平台服务（根据 settings.yaml 中的 OAuth 配置自动注册）
    register_platform_services()

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
