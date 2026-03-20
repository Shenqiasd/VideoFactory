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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from core.task import TaskStore
from core.runtime import read_worker_heartbeat
from api.routes.tasks import router as tasks_router
from api.routes.production import router as production_router
from api.routes.factory import router as factory_router
from api.routes.distribute import router as distribute_router
from api.routes.system import router as system_router
from api.routes.pages import router as pages_router
from api.routes.publish import router as publish_router
from api.routes.storage import router as storage_router
from api.routes.monitor import router as monitor_router
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

    yield

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

# 挂载静态文件
BASE_DIR = Path(__file__).resolve().parents[1]
static_dir = BASE_DIR / "web" / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

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
    store = TaskStore()
    stats = store.get_stats()
    heartbeat = read_worker_heartbeat(max_age_seconds=90)

    return {
        "status": "healthy",
        "service": "video-factory",
        "worker_alive": heartbeat["alive"],
        "last_worker_heartbeat": heartbeat["timestamp"],
        "worker_pid": heartbeat["pid"],
        "worker_reason": heartbeat["reason"],
        "queue": {
            "queued": stats.get("queued", 0),
            "active": len(store.list_active()),
            "failed": stats.get("failed", 0),
            "total": stats.get("total", 0),
        },
    }


# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


if __name__ == "__main__":
    import uvicorn
    api_host = os.environ.get("VF_API_HOST", "127.0.0.1")
    api_port = int(os.environ.get("VF_API_PORT", "9000"))
    uvicorn.run(
        "api.server:app",
        host=api_host,
        port=api_port,
        reload=True,
        log_level="info",
    )
