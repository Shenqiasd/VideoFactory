"""
异步发布任务队列 — 基于 asyncio.Queue 的轻量级任务调度。

功能：
- 多 worker 并发消费（默认 3 个）
- 指数退避重试（最多 3 次）
- 定时任务检查器（每 30 秒扫描 scheduled 任务）
- 任务状态持久化到 publish_tasks_v2 表
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from core.database import Database
from platform_services.registry import PlatformRegistry
from platform_services.token_manager import TokenManager
from platform_services.exceptions import PlatformError, TokenExpiredError

logger = logging.getLogger(__name__)


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
    异步发布任务队列。

    使用 asyncio.Queue 作为内存队列，asyncio.Semaphore 控制并发。
    任务状态持久化到 publish_tasks_v2 表。
    """

    MAX_CONCURRENCY = 3
    MAX_ATTEMPTS = 3
    BASE_RETRY_DELAY = 5  # seconds
    SCHEDULE_CHECK_INTERVAL = 30  # seconds

    def __init__(
        self,
        db: Database,
        token_manager: TokenManager,
        registry: Optional[PlatformRegistry] = None,
    ):
        self.db = db
        self.token_manager = token_manager
        # registry is a class-level singleton; keep param for testability
        self._registry = registry
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENCY)
        self._workers: list[asyncio.Task] = []
        self._schedule_task: Optional[asyncio.Task] = None
        self._running = False

    # ------------------------------------------------------------------
    # Registry helper
    # ------------------------------------------------------------------

    def _get_service(self, platform: str):
        if self._registry is not None:
            return self._registry.get(platform)
        return PlatformRegistry.get(platform)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def enqueue(self, task_data: dict) -> str:
        """
        创建发布任务并加入队列。

        若 task_data 包含 scheduled_at 且为未来时间，则标记为 scheduled，
        由 _schedule_checker 在到期时自动入队。
        """
        task_id = task_data.get("id") or str(uuid.uuid4())
        task_data["id"] = task_id

        scheduled_at = task_data.get("scheduled_at")
        if scheduled_at:
            try:
                sched_dt = datetime.fromisoformat(scheduled_at)
                if sched_dt > datetime.now():
                    task_data["status"] = PublishStatus.SCHEDULED.value
                    self.db.insert_publish_task_v2(task_data)
                    logger.info("任务已调度: id=%s, scheduled_at=%s", task_id, scheduled_at)
                    return task_id
            except (ValueError, TypeError):
                pass

        task_data["status"] = PublishStatus.PENDING.value
        self.db.insert_publish_task_v2(task_data)
        await self._queue.put(task_id)
        logger.info("任务已入队: id=%s", task_id)
        return task_id

    async def start(self) -> None:
        """启动 worker 和定时任务检查器。"""
        if self._running:
            return
        self._running = True
        for i in range(self.MAX_CONCURRENCY):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)
        self._schedule_task = asyncio.create_task(self._schedule_checker())
        logger.info("PublishQueue 已启动: %d workers", self.MAX_CONCURRENCY)

    async def stop(self) -> None:
        """优雅关闭。"""
        self._running = False
        # 向每个 worker 发送 sentinel
        for _ in self._workers:
            await self._queue.put("")
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

    async def retry_task(self, task_id: str) -> bool:
        """重置失败任务并重新入队。"""
        task = self.db.get_publish_task_v2(task_id)
        if not task or task["status"] != PublishStatus.FAILED.value:
            return False
        self.db.update_publish_task_v2(
            task_id,
            status=PublishStatus.PENDING.value,
            attempts=0,
            error_message="",
        )
        await self._queue.put(task_id)
        logger.info("任务已重新入队: id=%s", task_id)
        return True

    async def cancel_task(self, task_id: str) -> bool:
        """取消待处理/已调度的任务。"""
        task = self.db.get_publish_task_v2(task_id)
        if not task or task["status"] not in (
            PublishStatus.PENDING.value,
            PublishStatus.SCHEDULED.value,
        ):
            return False
        self.db.update_publish_task_v2(
            task_id,
            status=PublishStatus.CANCELLED.value,
        )
        logger.info("任务已取消: id=%s", task_id)
        return True

    # ------------------------------------------------------------------
    # Internal workers
    # ------------------------------------------------------------------

    async def _worker(self, worker_id: int) -> None:
        """消费循环：从队列取 task_id，处理任务。"""
        logger.info("Worker %d 已启动", worker_id)
        while self._running:
            try:
                task_id = await asyncio.wait_for(self._queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            if not task_id:
                # sentinel for shutdown
                break
            async with self._semaphore:
                try:
                    await self._process_task(task_id)
                except Exception:
                    logger.exception("Worker %d 处理任务异常: id=%s", worker_id, task_id)
        logger.info("Worker %d 已停止", worker_id)

    async def _process_task(self, task_id: str) -> None:
        """处理单个发布任务（含重试逻辑）。"""
        task = self.db.get_publish_task_v2(task_id)
        if not task:
            logger.warning("任务不存在: id=%s", task_id)
            return

        if task["status"] == PublishStatus.CANCELLED.value:
            logger.info("任务已取消，跳过: id=%s", task_id)
            return

        platform = task["platform"]
        account_id = task["account_id"]

        service = self._get_service(platform)
        if not service:
            self.db.update_publish_task_v2(
                task_id,
                status=PublishStatus.FAILED.value,
                error_message=f"平台 '{platform}' 未注册",
            )
            return

        attempts = task.get("attempts", 0)
        max_attempts = task.get("max_attempts", self.MAX_ATTEMPTS)

        # Mark as publishing
        self.db.update_publish_task_v2(
            task_id,
            status=PublishStatus.PUBLISHING.value,
            attempts=attempts + 1,
        )

        try:
            credential = await self.token_manager.get_valid_token(account_id, service)

            platform_options = task.get("platform_options", {})
            if isinstance(platform_options, str):
                platform_options = json.loads(platform_options) if platform_options else {}

            result = await service.publish_video(
                credential=credential,
                video_path=task.get("video_path", ""),
                title=task["title"],
                description=task.get("description", ""),
                tags=task.get("tags", []),
                cover_path=task.get("cover_path", ""),
                **platform_options,
            )

            if result.success:
                self.db.update_publish_task_v2(
                    task_id,
                    status=PublishStatus.PUBLISHED.value,
                    post_id=result.post_id,
                    permalink=result.permalink,
                    published_at=datetime.now().isoformat(),
                )
                logger.info("任务发布成功: id=%s, permalink=%s", task_id, result.permalink)
            else:
                raise PlatformError(result.error or "发布失败")

        except Exception as exc:
            current_attempts = attempts + 1
            if current_attempts < max_attempts:
                delay = self.BASE_RETRY_DELAY * (2 ** current_attempts)
                self.db.update_publish_task_v2(
                    task_id,
                    status=PublishStatus.PENDING.value,
                    error_message=str(exc),
                    attempts=current_attempts,
                )
                logger.warning(
                    "任务发布失败，%d 秒后重试 (%d/%d): id=%s, error=%s",
                    delay, current_attempts, max_attempts, task_id, exc,
                )
                await asyncio.sleep(delay)
                await self._queue.put(task_id)
            else:
                self.db.update_publish_task_v2(
                    task_id,
                    status=PublishStatus.FAILED.value,
                    error_message=str(exc),
                    attempts=current_attempts,
                )
                logger.error(
                    "任务发布最终失败 (%d/%d): id=%s, error=%s",
                    current_attempts, max_attempts, task_id, exc,
                )

    async def _schedule_checker(self) -> None:
        """定期检查已到期的 scheduled 任务并入队。"""
        logger.info("Schedule checker 已启动 (间隔 %ds)", self.SCHEDULE_CHECK_INTERVAL)
        while self._running:
            try:
                await asyncio.sleep(self.SCHEDULE_CHECK_INTERVAL)
                now = datetime.now().isoformat()
                tasks = self.db.get_publish_tasks_v2(status=PublishStatus.SCHEDULED.value)
                for task in tasks:
                    scheduled_at = task.get("scheduled_at")
                    if scheduled_at and scheduled_at <= now:
                        self.db.update_publish_task_v2(
                            task["id"],
                            status=PublishStatus.PENDING.value,
                        )
                        await self._queue.put(task["id"])
                        logger.info("调度任务已到期并入队: id=%s", task["id"])
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Schedule checker 异常")
