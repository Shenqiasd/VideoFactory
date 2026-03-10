"""
编排器 - 自动驱动任务从创建到发布的完整流程
这是video-factory的"大脑"，串联所有管线
"""
import asyncio
import logging
from typing import Optional

from core.task import Task, TaskState, TaskStore
from core.notification import NotificationManager, NotifyLevel
from core.config import Config
from production.pipeline import ProductionPipeline
from factory.pipeline import FactoryPipeline
from distribute.scheduler import PublishScheduler

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    全流程编排器
    自动驱动: 排队 → 生产 → 加工 → 发布

    可以作为后台服务持续运行，定期检查待处理任务
    """

    def __init__(
        self,
        task_store: Optional[TaskStore] = None,
        production: Optional[ProductionPipeline] = None,
        factory: Optional[FactoryPipeline] = None,
        scheduler: Optional[PublishScheduler] = None,
        notifier: Optional[NotificationManager] = None,
    ):
        self.task_store = task_store or TaskStore()
        self.production = production or ProductionPipeline(task_store=self.task_store)
        self.factory = factory or FactoryPipeline(task_store=self.task_store)
        self.scheduler = scheduler or PublishScheduler(task_store=self.task_store)
        self.notifier = notifier or NotificationManager()

        self._running = False
        self._max_concurrent = Config().get("tasks", "max_concurrent", default=2)

        # 启动时恢复僵尸任务：中间状态（无子进程驱动）回退到 QUEUED
        self._recover_stuck_tasks()

    def _recover_stuck_tasks(self):
        """
        Worker 重启后，把卡在中间状态的任务回退到 QUEUED，
        避免僵尸任务占着并发位又永远不会被处理。
        中间状态 = downloading / uploading_source / translating / processing / uploading_products / publishing
        这些状态需要有子进程在跑才有意义，Worker 刚启动时不可能有。
        """
        stuck_states = [
            TaskState.DOWNLOADING, TaskState.UPLOADING_SOURCE,
            TaskState.TRANSLATING, TaskState.PROCESSING,
            TaskState.UPLOADING_PRODUCTS, TaskState.PUBLISHING,
        ]
        recovered = 0
        for state in stuck_states:
            for task in self.task_store.list_by_state(state):
                old_state = task.state
                task.state = TaskState.QUEUED.value
                self.task_store.update(task)
                logger.warning(
                    f"♻️ 恢复僵尸任务: {task.task_id} ({old_state} → queued)"
                )
                recovered += 1

        if recovered:
            logger.info(f"♻️ 共恢复 {recovered} 个僵尸任务")

    async def process_task(self, task: Task) -> bool:
        """
        处理单个任务的完整流程

        Args:
            task: 待处理任务

        Returns:
            bool: 是否成功
        """
        logger.info(f"🔄 开始处理任务: {task.task_id} (状态: {task.state})")

        try:
            scope = getattr(task, "task_scope", "full")

            # Phase 1: 生产（翻译配音）— 所有 scope 都运行
            if task.state in [TaskState.QUEUED.value, TaskState.QC_FAILED.value]:
                success = await self.production.run(task)
                if not success:
                    logger.error(f"❌ 生产管线失败: {task.task_id}")
                    return False

            # Phase 2: 加工（二次创作）
            # subtitle_only 也需要跑加工，用于生成“无配音但有字幕”的长视频
            if task.state == TaskState.QC_PASSED.value:
                if scope in ("subtitle_only", "dub_and_copy", "full"):
                    success = await self.factory.run(task)
                    if not success:
                        logger.error(f"❌ 加工管线失败: {task.task_id}")
                        return False
                else:
                    # subtitle_dub: QC 通过即完成
                    task.transition(TaskState.COMPLETED)
                    self.task_store.update(task)
                    logger.info(f"✅ 任务完成 (scope={scope}): {task.task_id}")
                    return True

            # Phase 3: 发布 — 仅 full
            if task.state == TaskState.READY_TO_PUBLISH.value:
                creation_status = getattr(task, "creation_status", {}) or {}
                if (
                    creation_status.get("review_required")
                    and creation_status.get("review_status") != "approved"
                ):
                    logger.info(f"⏸️ 任务等待创作审核: {task.task_id}")
                    return True

                if scope == "full":
                    task.transition(TaskState.PUBLISHING)
                    self.task_store.update(task)

                    self.scheduler.schedule_staggered(
                        task,
                        platforms=["bilibili", "douyin", "xiaohongshu", "youtube"],
                        interval_minutes=30,
                    )
                    logger.info(f"📅 任务已调度发布: {task.task_id}")
                else:
                    # dub_and_copy: 加工完即完成，跳过发布
                    task.transition(TaskState.COMPLETED)
                    self.task_store.update(task)
                    logger.info(f"✅ 任务完成 (scope={scope}): {task.task_id}")
                    return True

            logger.info(f"✅ 任务处理完成: {task.task_id} (状态: {task.state}, scope={scope})")
            return True

        except Exception as e:
            logger.error(f"💥 任务处理异常: {task.task_id}: {e}")
            task.fail(str(e))
            self.task_store.update(task)
            await self.notifier.notify_error(task.task_id, str(e), "orchestrator")
            return False

    async def run_loop(self, check_interval: int = 30):
        """
        持续运行编排循环
        定期检查待处理任务并自动驱动

        Args:
            check_interval: 检查间隔（秒）
        """
        self._running = True
        logger.info(f"🚀 编排器启动，检查间隔: {check_interval}秒, 最大并发: {self._max_concurrent}")

        while self._running:
            try:
                # 重新加载任务数据（Web进程可能创建了新任务）
                self.task_store._load()

                # 查找待处理的任务（按优先级排序）
                queued_tasks = self.task_store.list_by_state(TaskState.QUEUED)
                qc_failed_tasks = self.task_store.list_by_state(TaskState.QC_FAILED)
                qc_passed_tasks = self.task_store.list_by_state(TaskState.QC_PASSED)
                ready_tasks = self.task_store.list_by_state(TaskState.READY_TO_PUBLISH)

                # 汇总需要处理的任务
                pending_tasks = queued_tasks + qc_failed_tasks + qc_passed_tasks + ready_tasks

                # 按优先级排序（0最高，3最低）
                pending_tasks.sort(key=lambda t: t.priority)

                # 计算当前"正在执行"的任务数（排除等待处理的状态）
                # 等待处理的状态不应占用并发位，否则会死锁
                executing_states = [
                    TaskState.DOWNLOADING, TaskState.DOWNLOADED,
                    TaskState.UPLOADING_SOURCE, TaskState.TRANSLATING,
                    TaskState.QC_CHECKING, TaskState.PROCESSING,
                    TaskState.UPLOADING_PRODUCTS, TaskState.PUBLISHING,
                ]
                executing_count = sum(
                    1 for t in self.task_store._tasks.values()
                    if t.state in [s.value for s in executing_states]
                )
                can_process = max(0, self._max_concurrent - executing_count)

                if pending_tasks and can_process > 0:
                    tasks_to_process = pending_tasks[:can_process]
                    logger.info(
                        f"📋 发现 {len(pending_tasks)} 个待处理任务，"
                        f"将处理 {len(tasks_to_process)} 个 "
                        f"(执行中: {executing_count}, 限制: {self._max_concurrent})"
                    )

                    # 并发处理
                    process_tasks = [
                        self.process_task(task)
                        for task in tasks_to_process
                    ]
                    await asyncio.gather(*process_tasks, return_exceptions=True)

            except Exception as e:
                logger.error(f"编排循环异常: {e}")

            await asyncio.sleep(check_interval)

    def stop(self):
        """停止编排器"""
        self._running = False
        logger.info("🛑 编排器已停止")

    async def close(self):
        """关闭所有资源"""
        self.stop()
        await self.production.close()
        await self.factory.close()
        await self.notifier.close()
