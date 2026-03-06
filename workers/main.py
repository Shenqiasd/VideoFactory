"""
Worker主入口 - 启动后台任务处理
可以单独运行，也可以和FastAPI一起运行
"""
import sys
import os
import asyncio
import logging
import signal
import contextlib

# 确保项目根目录和src都在Python路径中
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

from workers.orchestrator import Orchestrator
from distribute.scheduler import PublishScheduler
from core.config import Config
from core.runtime import write_worker_heartbeat

logger = logging.getLogger(__name__)


async def main():
    """启动所有后台服务"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("=" * 50)
    logger.info("video-factory Worker 启动")
    logger.info("=" * 50)

    # 初始化
    config = Config()
    heartbeat_interval = int(config.get("tasks", "worker_heartbeat_interval", default=10))

    orchestrator = Orchestrator()
    scheduler = PublishScheduler()
    worker_pid = os.getpid()
    heartbeat_task: asyncio.Task | None = None

    async def heartbeat_loop():
        while True:
            write_worker_heartbeat(
                pid=worker_pid,
                interval_seconds=heartbeat_interval,
                status="running",
                extra={"component": "worker"},
            )
            await asyncio.sleep(heartbeat_interval)

    # 信号处理
    loop = asyncio.get_event_loop()

    def shutdown():
        logger.info("收到关闭信号...")
        orchestrator.stop()
        scheduler.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown)

    try:
        write_worker_heartbeat(
            pid=worker_pid,
            interval_seconds=heartbeat_interval,
            status="running",
            extra={"component": "worker", "phase": "startup"},
        )
        heartbeat_task = asyncio.create_task(heartbeat_loop())

        # 并行运行编排器和发布调度器
        await asyncio.gather(
            orchestrator.run_loop(check_interval=30),
            scheduler.run_loop(check_interval=60),
        )
    except asyncio.CancelledError:
        logger.info("Worker被取消")
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

        write_worker_heartbeat(
            pid=worker_pid,
            interval_seconds=heartbeat_interval,
            status="stopped",
            extra={"component": "worker", "phase": "shutdown"},
        )
        await orchestrator.close()
        logger.info("Worker已关闭")


if __name__ == "__main__":
    asyncio.run(main())
