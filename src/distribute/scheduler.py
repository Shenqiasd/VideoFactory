"""
发布调度器 - 管理定时发布和发布队列
"""
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from core.task import Task, TaskState, TaskStore
from core.notification import NotificationManager, NotifyLevel
from core.config import Config
from distribute.publisher import PublishManager

logger = logging.getLogger(__name__)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class PublishJob:
    """发布任务"""

    def __init__(
        self,
        task_id: str,
        platform: str,
        scheduled_time: float,
        product: Dict[str, Any],
        metadata: Dict[str, Any] = None,
        max_retries: int = 2,
    ):
        self.task_id = task_id
        self.platform = platform
        self.scheduled_time = scheduled_time  # Unix timestamp
        self.product = product
        self.metadata = metadata or {}
        self.product_type = product.get("type", "unknown")
        self.product_identity = (
            product.get("r2_path")
            or product.get("local_path")
            or product.get("title")
            or "default"
        )
        # 包含 task/platform/type 以支持跨请求去重，同时用 product_identity 避免多短视频被错误合并
        self.idempotency_key = f"{task_id}:{platform}:{self.product_type}:{self.product_identity}"
        self.status = "pending"  # pending / publishing / done / failed
        self.result: Dict[str, Any] = {}
        self.retry_count = 0
        self.max_retries = max_retries

    def is_due(self) -> bool:
        return time.time() >= self.scheduled_time

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "platform": self.platform,
            "scheduled_time": self.scheduled_time,
            "product": self.product,
            "metadata": self.metadata,
            "product_type": self.product_type,
            "product_identity": self.product_identity,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "result": self.result,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PublishJob":
        job = cls(
            task_id=data["task_id"],
            platform=data["platform"],
            scheduled_time=data["scheduled_time"],
            product=data["product"],
            metadata=data.get("metadata", {}),
            max_retries=data.get("max_retries", 2),
        )
        job.product_type = data.get("product_type", job.product_type)
        job.product_identity = data.get("product_identity", job.product_identity)
        job.idempotency_key = data.get("idempotency_key", job.idempotency_key)
        job.status = data.get("status", "pending")
        job.result = data.get("result", {})
        job.retry_count = data.get("retry_count", 0)
        return job


class PublishScheduler:
    """
    发布调度器
    支持即时发布和定时发布
    """

    def __init__(
        self,
        task_store: Optional[TaskStore] = None,
        publish_manager: Optional[PublishManager] = None,
        notifier: Optional[NotificationManager] = None,
        queue_file: str = None,
    ):
        self.task_store = task_store or TaskStore()
        self.publish_manager = publish_manager or PublishManager()
        self.notifier = notifier or NotificationManager()

        config = Config()
        self.max_retries = _safe_int(config.get("distribute", "publish_max_retries", default=2), 2)
        self.retry_backoff_seconds = _safe_int(config.get("distribute", "retry_backoff_seconds", default=60), 60)

        if queue_file is None:
            queue_file = str(Path.home() / ".video-factory" / "publish_queue.json")
        self.queue_file = Path(queue_file)
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)

        self._queue: List[PublishJob] = []
        self._running = False
        self._load_queue()

    def _is_product_platform_match(self, product_type: str, platform: str) -> bool:
        if product_type == "long_video":
            return platform in ["bilibili", "youtube"]
        if product_type == "short_clip":
            return platform in ["douyin", "xiaohongshu"]
        if product_type == "article":
            return platform in ["xiaohongshu"]
        if product_type == "cover":
            return False
        return True

    def _find_idempotency_job(self, key: str) -> Optional[PublishJob]:
        for job in self._queue:
            if job.idempotency_key == key and job.status in ("pending", "publishing", "done"):
                return job
        return None

    def _enqueue_job(self, job: PublishJob, force: bool = False) -> Tuple[bool, str]:
        existing = self._find_idempotency_job(job.idempotency_key)
        if existing and not force:
            return False, f"duplicate:{existing.status}"
        self._queue.append(job)
        return True, "enqueued"

    def schedule_immediate(self, task: Task, platforms: List[str] = None, force: bool = False) -> Dict[str, int]:
        if platforms is None:
            platforms = ["bilibili", "douyin", "xiaohongshu", "youtube"]

        now = time.time()
        added = 0
        skipped = 0

        for product in task.products:
            ptype = product.get("type", "")
            for platform in platforms:
                if not self._is_product_platform_match(ptype, platform):
                    continue
                job = PublishJob(
                    task_id=task.task_id,
                    platform=platform,
                    scheduled_time=now,
                    product=product,
                    metadata=product.get("metadata", {}),
                    max_retries=self.max_retries,
                )
                ok, _ = self._enqueue_job(job, force=force)
                if ok:
                    added += 1
                else:
                    skipped += 1

        self._save_queue()
        logger.info("📅 立即发布调度: added=%s skipped=%s", added, skipped)
        return {"added": added, "skipped": skipped}

    def schedule_timed(
        self,
        task: Task,
        publish_times: Dict[str, float],
        force: bool = False,
    ) -> Dict[str, int]:
        added = 0
        skipped = 0

        for product in task.products:
            ptype = product.get("type", "")

            for platform, publish_time in publish_times.items():
                if not self._is_product_platform_match(ptype, platform):
                    continue

                job = PublishJob(
                    task_id=task.task_id,
                    platform=platform,
                    scheduled_time=publish_time,
                    product=product,
                    metadata=product.get("metadata", {}),
                    max_retries=self.max_retries,
                )
                ok, _ = self._enqueue_job(job, force=force)
                if ok:
                    added += 1
                else:
                    skipped += 1

        self._save_queue()
        logger.info("📅 定时发布调度: added=%s skipped=%s", added, skipped)
        return {"added": added, "skipped": skipped}

    def schedule_staggered(
        self,
        task: Task,
        platforms: List[str] = None,
        interval_minutes: int = 30,
        start_delay_minutes: int = 0,
        force: bool = False,
    ) -> Dict[str, int]:
        if platforms is None:
            platforms = ["bilibili", "douyin", "xiaohongshu", "youtube"]

        base_time = time.time() + start_delay_minutes * 60
        added = 0
        skipped = 0

        for i, platform in enumerate(platforms):
            publish_time = base_time + i * interval_minutes * 60

            for product in task.products:
                ptype = product.get("type", "")
                if not self._is_product_platform_match(ptype, platform):
                    continue

                job = PublishJob(
                    task_id=task.task_id,
                    platform=platform,
                    scheduled_time=publish_time,
                    product=product,
                    metadata=product.get("metadata", {}),
                    max_retries=self.max_retries,
                )
                ok, _ = self._enqueue_job(job, force=force)
                if ok:
                    added += 1
                else:
                    skipped += 1

        self._save_queue()
        logger.info(
            "📅 错峰发布调度完成: added=%s skipped=%s interval_minutes=%s",
            added,
            skipped,
            interval_minutes,
        )
        return {"added": added, "skipped": skipped}

    async def run_loop(self, check_interval: int = 60):
        self._running = True
        logger.info("🔄 发布调度器已启动")

        while self._running:
            try:
                due_jobs = [j for j in self._queue if j.status == "pending" and j.is_due()]
                for job in due_jobs:
                    await self._execute_job(job)
            except Exception as e:
                logger.error(f"调度循环异常: {e}")

            await asyncio.sleep(check_interval)

    def _retry_delay(self, retry_count: int) -> int:
        return min(3600, self.retry_backoff_seconds * (2 ** max(0, retry_count - 1)))

    async def _execute_job(self, job: PublishJob):
        job.status = "publishing"
        self._save_queue()

        logger.info(
            "📤 执行发布: platform=%s task_id=%s key=%s retry=%s/%s",
            job.platform,
            job.task_id,
            job.idempotency_key,
            job.retry_count,
            job.max_retries,
        )

        try:
            video_path = job.product.get("local_path", "")
            title = job.metadata.get("title", job.product.get("title", ""))
            description = job.metadata.get("description", job.product.get("description", ""))
            tags = job.metadata.get("tags", job.product.get("tags", []))
            cover_path = job.product.get("cover_path", "")

            result = await self.publish_manager.publish_to_platform(
                platform=job.platform,
                video_path=video_path,
                title=title,
                description=description,
                tags=tags,
                cover_path=cover_path,
                task_id=job.task_id,
                product_type=job.product_type,
                idempotency_key=job.idempotency_key,
                r2_path=job.product.get("r2_path", ""),
            )
            job.result = result

            if result.get("success"):
                job.status = "done"
                await self.notifier.notify(
                    "发布成功",
                    f"平台: {job.platform}\n标题: {title}\nURL: {result.get('url', 'N/A')}",
                    NotifyLevel.SUCCESS,
                    job.task_id,
                )
            else:
                await self._handle_failure(job, result.get("error", "未知错误"))
        except Exception as e:
            job.result = {"success": False, "error": str(e)}
            await self._handle_failure(job, str(e))

        self._save_queue()
        await self._check_task_completion(job.task_id)

    async def _handle_failure(self, job: PublishJob, error: str):
        if job.retry_count < job.max_retries:
            job.retry_count += 1
            delay = self._retry_delay(job.retry_count)
            job.status = "pending"
            job.scheduled_time = time.time() + delay
            job.result.update({"success": False, "error": error, "retry_in_seconds": delay})

            await self.notifier.notify(
                "发布重试中",
                (
                    f"平台: {job.platform}\n错误: {error}\n"
                    f"将于 {delay} 秒后重试 ({job.retry_count}/{job.max_retries})"
                ),
                NotifyLevel.WARNING,
                job.task_id,
            )
            return

        job.status = "failed"
        await self.notifier.notify(
            "发布失败",
            f"平台: {job.platform}\n错误: {error}",
            NotifyLevel.ERROR,
            job.task_id,
        )

    async def _check_task_completion(self, task_id: str):
        task_jobs = [j for j in self._queue if j.task_id == task_id]
        pending = [j for j in task_jobs if j.status in ("pending", "publishing")]

        if pending:
            return

        task = self.task_store.get(task_id)
        if not task or task.state != TaskState.PUBLISHING.value:
            return

        all_done = [j for j in task_jobs if j.status == "done"]
        if len(all_done) == len(task_jobs):
            task.transition(TaskState.COMPLETED)
        else:
            failed = [j for j in task_jobs if j.status == "failed"]
            if failed:
                task.error_message = f"{len(failed)}/{len(task_jobs)} 平台发布失败"
            task.transition(TaskState.COMPLETED)

        self.task_store.update(task)
        await self.notifier.notify_completion(
            task_id,
            products_count=len(task.products),
            duration_seconds=task.duration_seconds,
        )

    def replay_failed(
        self,
        task_id: str,
        platform: Optional[str] = None,
        product_type: Optional[str] = None,
    ) -> int:
        replayed = 0
        now = time.time()
        for job in self._queue:
            if job.task_id != task_id or job.status != "failed":
                continue
            if platform and job.platform != platform:
                continue
            if product_type and job.product_type != product_type:
                continue
            job.status = "pending"
            job.retry_count = 0
            job.scheduled_time = now
            replayed += 1

        if replayed:
            self._save_queue()
        return replayed

    def stop(self):
        self._running = False

    def get_queue_status(self) -> Dict[str, int]:
        status_count: Dict[str, int] = {}
        for job in self._queue:
            status_count[job.status] = status_count.get(job.status, 0) + 1
        return status_count

    def _load_queue(self):
        if self.queue_file.exists():
            try:
                data = json.loads(self.queue_file.read_text())
                self._queue = [PublishJob.from_dict(d) for d in data]
            except Exception as e:
                logger.warning(f"加载发布队列失败: {e}")

    def _save_queue(self):
        try:
            data = [j.to_dict() for j in self._queue]
            self.queue_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning(f"保存发布队列失败: {e}")
