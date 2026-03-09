"""
存储清理定时任务调度器。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from core.config import Config
from core.storage import StorageManager, LocalStorage

logger = logging.getLogger(__name__)

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
except Exception:  # pragma: no cover - optional dependency
    AsyncIOScheduler = None
    CronTrigger = None


class StorageCleanupScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler() if AsyncIOScheduler else None
        self.config = Config()

    def start(self):
        """启动定时任务"""
        cleanup_config = self.config.get("storage", "auto_cleanup", default={}) or {}
        if not cleanup_config.get("enabled", False):
            logger.info("自动清理未启用")
            return

        schedule = cleanup_config.get("schedule", "0 2 * * *")
        if not self.scheduler or not CronTrigger:
            logger.warning("APScheduler 未安装，存储清理定时任务未启动")
            return

        try:
            trigger = CronTrigger.from_crontab(schedule)
        except Exception as exc:
            logger.error(f"清理定时表达式无效: {schedule} ({exc})")
            return

        self.scheduler.add_job(
            self.run_cleanup,
            trigger=trigger,
            id="storage_cleanup",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info(f"✅ 存储清理定时任务已启动: {schedule}")

    def shutdown(self):
        if self.scheduler:
            self.scheduler.shutdown(wait=False)

    def _run_rules(self, rules: List[Dict[str, Any]]):
        for rule in rules:
            if not rule.get("enabled", True):
                continue
            location = str(rule.get("location", "")).strip().lower()
            path = str(rule.get("path", "")).strip()
            days = rule.get("days")
            if not location or not path or days is None:
                continue
            try:
                days = int(days)
            except (TypeError, ValueError):
                continue

            try:
                if location == "r2":
                    storage = StorageManager(
                        bucket=self.config.get("storage", "r2", "bucket", default="videoflow"),
                        rclone_remote=self.config.get("storage", "r2", "rclone_remote", default="r2"),
                    )
                    result = storage.cleanup_old_files(path, days)
                elif location == "local":
                    local_storage = LocalStorage(
                        working_dir=self.config.get("storage", "local", "mac_working_dir", default="/tmp/video-factory/working"),
                        output_dir=self.config.get("storage", "local", "mac_output_dir", default="/tmp/video-factory/output"),
                    )
                    result = local_storage.cleanup_old_files(path, days)
                else:
                    continue

                logger.info(
                    "✅ 清理完成: %s:%s, 删除 %s 个文件, 释放 %s",
                    location,
                    path,
                    result.get("deleted", 0),
                    result.get("freed_human", "0 B"),
                )
            except Exception as exc:
                logger.error("清理失败: %s:%s, %s", location, path, exc)

    def run_cleanup(self):
        """执行清理任务"""
        cleanup_config = self.config.get("storage", "auto_cleanup", default={}) or {}
        rules = cleanup_config.get("rules", []) if isinstance(cleanup_config, dict) else []
        if not isinstance(rules, list):
            return
        self._run_rules(rules)
