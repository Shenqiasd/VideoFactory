"""
Token 管理器：进程内 TTL 缓存 + 数据库持久化 + 自动刷新。

设计参考 AiToEarn 的 Redis + MongoDB 双层存储，
简化为 cachetools.TTLCache + SQLite/PostgreSQL 单层持久化。
"""

import logging
import time
from typing import Optional

from cachetools import TTLCache

from .base import OAuthCredential, PlatformService
from .exceptions import TokenExpiredError

logger = logging.getLogger(__name__)


class TokenManager:
    """
    Token 生命周期管理器。

    - 进程内 TTL 缓存（maxsize=1000, ttl=30min）避免频繁读库
    - 自动刷新：token 剩余时间 < REFRESH_BUFFER_SECONDS 时调用 refresh_token
    - 双写：缓存 + 数据库同步更新
    """

    REFRESH_BUFFER_SECONDS = 600  # 过期前 10 分钟触发刷新

    def __init__(self, db):
        self.db = db
        self._cache: TTLCache = TTLCache(maxsize=1000, ttl=1800)

    async def get_valid_token(
        self,
        account_id: str,
        platform_service: PlatformService,
    ) -> OAuthCredential:
        """
        获取有效的 token，自动刷新过期 token。

        查找顺序：缓存 → 数据库 → 刷新 → 抛异常
        """
        # 1. 查缓存
        credential: Optional[OAuthCredential] = self._cache.get(account_id)

        # 2. 缓存未命中 → 查数据库
        if credential is None:
            db_record = self.db.get_oauth_credential(account_id)
            if db_record is None:
                raise TokenExpiredError(
                    f"No credential found for account {account_id}"
                )
            credential = OAuthCredential(
                access_token=db_record["access_token"],
                refresh_token=db_record["refresh_token"],
                expires_at=db_record["expires_at"],
                refresh_expires_at=db_record.get("refresh_expires_at"),
                raw=db_record.get("raw"),
            )

        # 3. 检查是否需要刷新
        if not await platform_service.check_token_status(credential):
            if not credential.refresh_token:
                raise TokenExpiredError(
                    f"Token expired and no refresh_token for account {account_id}"
                )
            logger.info("Token 即将过期，正在刷新: account_id=%s", account_id)
            credential = await platform_service.refresh_token(credential)
            self.save_credential(
                account_id, platform_service.platform.value, credential,
            )

        # 4. 写入缓存
        self._cache[account_id] = credential
        return credential

    def save_credential(
        self,
        account_id: str,
        platform: str,
        credential: OAuthCredential,
    ) -> None:
        """同时写入缓存和数据库。"""
        self._cache[account_id] = credential
        self.db.upsert_oauth_credential(
            account_id=account_id,
            platform=platform,
            access_token=credential.access_token,
            refresh_token=credential.refresh_token,
            expires_at=credential.expires_at,
            refresh_expires_at=credential.refresh_expires_at,
            raw=credential.raw or "",
        )

    def invalidate(self, account_id: str) -> None:
        """清除缓存中的 token（解绑账号时调用）。"""
        self._cache.pop(account_id, None)

    def cache_stats(self) -> dict:
        """返回缓存统计信息（调试用）。"""
        return {
            "size": len(self._cache),
            "maxsize": self._cache.maxsize,
            "ttl": self._cache.ttl,
        }
