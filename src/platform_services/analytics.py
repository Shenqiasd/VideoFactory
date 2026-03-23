"""
内容数据分析服务 — 从各平台 API 拉取视频统计数据。

提供：
- 单个视频统计数据拉取
- 批量同步所有已发布内容数据
- 跨平台数据汇总
- 单任务历史数据查询
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class AnalyticsService:
    """内容数据分析服务 — 从各平台 API 拉取视频统计数据。"""

    def __init__(self, db, token_manager, registry):
        self.db = db
        self.token_manager = token_manager
        self.registry = registry

    async def fetch_video_stats(
        self, account_id: str, platform: str, post_id: str,
    ) -> dict:
        """从平台 API 获取单个视频的统计数据（views, likes, comments, shares）。"""
        service = self.registry.get(platform)
        if not service:
            logger.debug("平台 %s 未注册，跳过统计拉取", platform)
            return {}
        credential = await self.token_manager.get_valid_token(account_id, service)
        try:
            return await service.get_video_stats(credential, post_id)
        except NotImplementedError:
            logger.debug("平台 %s 未实现 get_video_stats", platform)
            return {}

    async def sync_all_stats(self) -> dict:
        """批量同步所有已发布内容的数据。Returns {"synced": N, "failed": N, "skipped": N}。"""
        published = self.db.get_publish_tasks_v2(status="published")
        results = {"synced": 0, "failed": 0, "skipped": 0}
        for task in published:
            if not task.get("post_id"):
                results["skipped"] += 1
                continue
            try:
                stats = await self.fetch_video_stats(
                    task["account_id"], task["platform"], task["post_id"],
                )
                if stats:
                    self.db.upsert_content_analytics(
                        publish_task_id=task["id"],
                        platform=task["platform"],
                        post_id=task["post_id"],
                        views=stats.get("views", 0),
                        likes=stats.get("likes", 0),
                        comments=stats.get("comments", 0),
                        shares=stats.get("shares", 0),
                        raw_data=stats,
                    )
                    results["synced"] += 1
                else:
                    results["skipped"] += 1
            except Exception:
                logger.exception(
                    "同步统计失败: task=%s platform=%s",
                    task["id"], task["platform"],
                )
                results["failed"] += 1
        logger.info("数据同步完成: %s", results)
        return results

    def get_analytics_summary(self) -> dict:
        """获取跨平台数据汇总（按平台分组的总 views/likes/comments/shares）。"""
        return self.db.get_analytics_summary()

    def get_task_analytics(self, publish_task_id: str) -> list:
        """获取单个发布任务的历史数据记录。"""
        return self.db.get_content_analytics(publish_task_id)

    def get_top_content(self, limit: int = 10) -> list:
        """获取热门内容（按播放量排序）。"""
        return self.db.get_top_content(limit=limit)
