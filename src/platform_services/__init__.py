"""
多平台认证发布 — 平台抽象层。

借鉴 AiToEarn 的 PlatformBaseService 模式，
在 Python/FastAPI 框架内实现统一的 OAuth + 发布接口。
"""

from .base import (
    AuthMethod,
    OAuthCredential,
    PlatformAccount,
    PlatformService,
    PlatformType,
    PublishResult,
)
from .exceptions import (
    OAuthError,
    PlatformError,
    PublishError,
    TokenExpiredError,
)
from .registry import PlatformRegistry
from .token_manager import TokenManager

__all__ = [
    "AuthMethod",
    "OAuthCredential",
    "OAuthError",
    "PlatformAccount",
    "PlatformError",
    "PlatformRegistry",
    "PlatformService",
    "PlatformType",
    "PublishError",
    "PublishResult",
    "TokenExpiredError",
    "TokenManager",
]
