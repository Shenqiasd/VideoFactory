"""
通知模块 - 统一管理飞书/日志通知
"""
import logging
import httpx
import asyncio
from typing import Optional, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)


class NotifyLevel(str, Enum):
    """通知级别"""
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class NotificationManager:
    """
    通知管理器
    支持飞书webhook、飞书机器人消息、日志
    """

    def __init__(self, feishu_webhook: str = "", feishu_app_id: str = "", feishu_app_secret: str = ""):
        self.feishu_webhook = feishu_webhook
        self.feishu_app_id = feishu_app_id
        self.feishu_app_secret = feishu_app_secret
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30)
        return self._client

    async def notify(self, title: str, content: str, level: NotifyLevel = NotifyLevel.INFO, task_id: str = ""):
        """
        发送通知

        Args:
            title: 通知标题
            content: 通知内容
            level: 通知级别
            task_id: 关联的任务ID
        """
        # 1. 始终记录日志
        level_map = {
            NotifyLevel.INFO: logger.info,
            NotifyLevel.SUCCESS: logger.info,
            NotifyLevel.WARNING: logger.warning,
            NotifyLevel.ERROR: logger.error,
        }
        log_fn = level_map.get(level, logger.info)
        prefix = f"[{task_id}] " if task_id else ""
        log_fn(f"{prefix}{title}: {content}")

        # 2. 如果配置了飞书webhook，发送飞书通知
        if self.feishu_webhook:
            await self._send_feishu_webhook(title, content, level, task_id)

    async def _send_feishu_webhook(self, title: str, content: str, level: NotifyLevel, task_id: str = ""):
        """通过飞书webhook发送通知"""
        emoji_map = {
            NotifyLevel.INFO: "📋",
            NotifyLevel.SUCCESS: "✅",
            NotifyLevel.WARNING: "⚠️",
            NotifyLevel.ERROR: "❌",
        }
        emoji = emoji_map.get(level, "📋")

        card = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"{emoji} {title}"
                    }
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": content
                        }
                    }
                ]
            }
        }

        if task_id:
            card["card"]["elements"].append({
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": f"Task: {task_id}"
                    }
                ]
            })

        try:
            client = await self._get_client()
            resp = await client.post(self.feishu_webhook, json=card)
            if resp.status_code == 200:
                logger.debug(f"飞书通知发送成功: {title}")
            else:
                logger.warning(f"飞书通知发送失败: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"飞书通知发送异常: {e}")

    async def notify_task_state_change(self, task_id: str, old_state: str, new_state: str, extra: str = ""):
        """任务状态变更通知"""
        level = NotifyLevel.INFO
        if new_state == "completed":
            level = NotifyLevel.SUCCESS
        elif new_state == "failed":
            level = NotifyLevel.ERROR

        content = f"状态: {old_state} → {new_state}"
        if extra:
            content += f"\n{extra}"

        await self.notify(
            title=f"任务状态变更",
            content=content,
            level=level,
            task_id=task_id
        )

    async def notify_error(self, task_id: str, error: str, stage: str = ""):
        """错误通知"""
        content = f"阶段: {stage}\n错误: {error}" if stage else f"错误: {error}"
        await self.notify(
            title="任务出错",
            content=content,
            level=NotifyLevel.ERROR,
            task_id=task_id
        )

    async def notify_completion(self, task_id: str, products_count: int = 0, duration_seconds: float = 0):
        """任务完成通知"""
        minutes = int(duration_seconds / 60)
        content = f"产出物数量: {products_count}\n耗时: {minutes} 分钟"
        await self.notify(
            title="任务完成",
            content=content,
            level=NotifyLevel.SUCCESS,
            task_id=task_id
        )

    async def close(self):
        """关闭客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None
