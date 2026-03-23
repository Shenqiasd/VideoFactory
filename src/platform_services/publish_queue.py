"""
异步发布任务队列。

- asyncio.Queue 作为内存队列
- Semaphore 控制最大并发
- 指数退避重试
- 定时检查 scheduled 任务
"""

import asyncio
import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from .exceptions import PlatformError
from .registry import PlatformRegistry
from .token_manager import TokenManager

logger = logging.getLogger(__name__)

MAX_CONCURRENCY = 3
SCHEDULE_CHECK_INTERVAL = 30  # seconds
BASE_RETRY_DELAY = 5  # seconds
MAX_ATTEMPTS = 3


class PublishStatus(str, Enum):
    """发布任务状态。"""
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"


class PublishQueue:
    """
    异步发布队列。

    使用 asyncio.Queue 驱动，Semaphore 限并发，
    后台 worker 消费任务 ID 并调用平台 publish_video。
    """

    def __init__(self, db, token_manager: TokenManager, registry: type = PlatformRegistry):
        self.db = db
        self.token_manager = token_manager
        self.registry = registry
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        self._workers: list[asyncio.Task] = []
        self._schedule_task: Optional[asyncio.Task] = None
        self._running = False

    async def enqueue(self, task_data: dict) -> str:
        """
        创建发布任务并加入队列。

        如果 scheduled_at 是未来时间，标记为 scheduled 状态，
        由 _schedule_checker 在到期后入队。
        """
        task_id = task_data.get("id") or str(uuid.uuid4())
        task_data["id"] = task_id

        scheduled_at = task_data.get("scheduled_at")
        if scheduled_at:
            try:
                scheduled_dt = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
                now_dt = datetime.now(scheduled_dt.tzinfo) if scheduled_dt.tzinfo else datetime.now()
                if scheduled_dt > now_dt:
                    task_data["status"] = PublishStatus.SCHEDULED.value
                    self.db.insert_publish_task_v2(task_data)
                    logger.info("任务已加入定时队列: %s (scheduled_at=%s)", task_id, scheduled_at)
                    return task_id
            except (ValueError, TypeError):
                pass

        task_data["status"] = PublishStatus.PENDING.value
        self.db.insert_publish_task_v2(task_data)
        await self._queue.put(task_id)
        logger.info("任务已入队: %s", task_id)
        return task_id

    async def start(self) -> None:
        """启动 worker 和定时检查器。"""
        if self._running:
            return
        self._running = True
        for i in range(MAX_CONCURRENCY):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)
        self._schedule_task = asyncio.create_task(self._schedule_checker())
        logger.info("PublishQueue 已启动: %d workers", MAX_CONCURRENCY)

    async def stop(self) -> None:
        """优雅关闭。"""
        self._running = False
        # 给每个 worker 发送 sentinel 使其退出
        for _ in self._workers:
            await self._queue.put(None)
        for w in self._workers:
            w.cancel()
            try:
                await w
            except asyncio.CancelledError:
                pass
        self._workers.clear()
        if self._schedule_task:
            self._schedule_task.cancel()
            try:
                await self._schedule_task
            except asyncio.CancelledError:
                pass
            self._schedule_task = None
        logger.info("PublishQueue 已停止")

    async def _worker(self, worker_id: int) -> None:
        """消费者循环：从队列取 task_id 并处理。"""
        logger.info("Worker-%d 启动", worker_id)
        while self._running:
            try:
                task_id = await asyncio.wait_for(self._queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            if task_id is None:
                break
            async with self._semaphore:
                try:
                    await self._process_task(task_id)
                except Exception:
                    logger.exception("Worker-%d 处理任务 %s 时出错", worker_id, task_id)
        logger.info("Worker-%d 已退出", worker_id)

    async def _process_task(self, task_id: str) -> None:
        """
        处理单个发布任务。

        1. 从 DB 加载任务
        2. 标记为 publishing
        3. 获取有效凭证
        4. 调用平台 publish_video
        5. 更新状态
        6. 失败时指数退避重试
        """
        task = self.db.get_publish_task_v2(task_id)
        if not task:
            logger.warning("任务不存在: %s", task_id)
            return

        if task["status"] in (PublishStatus.PUBLISHED.value, PublishStatus.CANCELLED.value):
            logger.info("任务 %s 状态为 %s，跳过处理", task_id, task["status"])
            return

        self.db.update_publish_task_v2(task_id, status=PublishStatus.PUBLISHING.value)

        platform_name = task["platform"]
        service = self.registry.get(platform_name)
        if not service:
            self.db.update_publish_task_v2(
                task_id,
                status=PublishStatus.FAILED.value,
                error_message=f"平台 {platform_name} 未注册",
            )
            logger.error("平台 %s 未注册，任务 %s 失败", platform_name, task_id)
            return

        try:
            credential = await self.token_manager.get_valid_token(
                task["account_id"], service,
            )
            result = await service.publish_video(
                credential=credential,
                video_path=task.get("video_path", ""),
                title=task["title"],
                description=task.get("description", ""),
                tags=task.get("tags", []),
                cover_path=task.get("cover_path", ""),
                **(task.get("platform_options") or {}),
            )

            if result.success:
                self.db.update_publish_task_v2(
                    task_id,
                    status=PublishStatus.PUBLISHED.value,
                    post_id=result.post_id,
                    permalink=result.permalink,
                    published_at=datetime.now().isoformat(),
                )
                logger.info("任务 %s 发布成功: %s", task_id, result.permalink)
            else:
                raise PlatformError(result.error or "发布失败")

        except Exception as exc:
            attempts = (task.get("attempts") or 0) + 1
            max_attempts = task.get("max_attempts", MAX_ATTEMPTS)

            if attempts >= max_attempts:
                self.db.update_publish_task_v2(
                    task_id,
                    status=PublishStatus.FAILED.value,
                    attempts=attempts,
                    error_message=str(exc),
                )
                logger.error("任务 %s 达到最大重试次数 (%d)，标记失败: %s", task_id, max_attempts, exc)
            else:
                delay = BASE_RETRY_DELAY * (2 ** (attempts - 1))
                self.db.update_publish_task_v2(
                    task_id,
                    status=PublishStatus.PENDING.value,
                    attempts=attempts,
                    error_message=str(exc),
                )
                logger.warning(
                    "任务 %s 第 %d 次失败，%d 秒后重试: %s",
                    task_id, attempts, delay, exc,
                )
                await asyncio.sleep(delay)
                await self._queue.put(task_id)

    async def _schedule_checker(self) -> None:
        """每 30 秒检查一次定时任务，到期的入队。"""
        logger.info("定时任务检查器已启动 (间隔=%ds)", SCHEDULE_CHECK_INTERVAL)
        while self._running:
            try:
                await asyncio.sleep(SCHEDULE_CHECK_INTERVAL)
                scheduled = self.db.get_publish_tasks_v2(status=PublishStatus.SCHEDULED.value)
                for task in scheduled:
                    scheduled_at = task.get("scheduled_at")
                    if not scheduled_at:
                        continue
                    try:
                        sched_dt = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
                        now_dt = datetime.now(sched_dt.tzinfo) if sched_dt.tzinfo else datetime.now()
                    except (ValueError, TypeError):
                        continue
                    if sched_dt <= now_dt:
                        self.db.update_publish_task_v2(
                            task["id"], status=PublishStatus.PENDING.value,
                        )
                        await self._queue.put(task["id"])
                        logger.info("定时任务 %s 已到期，入队处理", task["id"])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("定时任务检查器出错")

    async def retry_task(self, task_id: str) -> bool:
        """重置失败任务并重新入队。"""
        task = self.db.get_publish_task_v2(task_id)
        if not task:
            return False
        if task["status"] != PublishStatus.FAILED.value:
            return False
        self.db.update_publish_task_v2(
            task_id,
            status=PublishStatus.PENDING.value,
            attempts=0,
            error_message="",
        )
        await self._queue.put(task_id)
        logger.info("任务 %s 已重置并重新入队", task_id)
        return True

    async def cancel_task(self, task_id: str) -> bool:
        """取消任务。"""
        task = self.db.get_publish_task_v2(task_id)
        if not task:
            return False
        if task["status"] in (PublishStatus.PUBLISHED.value,):
            return False
        self.db.update_publish_task_v2(
            task_id, status=PublishStatus.CANCELLED.value,
        )
        logger.info("任务 %s 已取消", task_id)
        return True
