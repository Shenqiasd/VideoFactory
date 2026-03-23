"""
平台服务注册表。

所有平台服务在应用启动时注册到此处，
OAuth 路由和发布队列通过 platform 名称查找对应的服务实例。
"""

import logging
from typing import Dict, List, Optional

from .base import PlatformService

logger = logging.getLogger(__name__)


class PlatformRegistry:
    """平台服务注册表（全局单例模式）。"""

    _services: Dict[str, PlatformService] = {}

    @classmethod
    def register(cls, service: PlatformService) -> None:
        """注册一个平台服务实例。"""
        key = service.platform.value
        cls._services[key] = service
        logger.info("平台注册: %s (auth=%s)", key, service.auth_method.value)

    @classmethod
    def get(cls, platform: str) -> Optional[PlatformService]:
        """按平台名称获取服务实例，不存在则返回 None。"""
        return cls._services.get(platform)

    @classmethod
    def list_platforms(cls) -> List[dict]:
        """返回所有已注册平台的摘要列表。"""
        return [
            {
                "platform": s.platform.value,
                "auth_method": s.auth_method.value,
            }
            for s in cls._services.values()
        ]

    @classmethod
    def clear(cls) -> None:
        """清空注册表（仅测试用）。"""
        cls._services.clear()
