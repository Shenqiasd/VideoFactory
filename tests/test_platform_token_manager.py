"""
Sprint 1: TokenManager 单元测试。

使用 mock 数据库和 mock 平台服务来隔离测试。
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from platform_services.base import AuthMethod, OAuthCredential, PlatformAccount, PlatformService, PlatformType, PublishResult  # noqa: E402
from platform_services.token_manager import TokenManager  # noqa: E402
from platform_services.exceptions import TokenExpiredError  # noqa: E402


class MockPlatformService(PlatformService):
    """Mock 平台服务。"""
    platform = PlatformType.YOUTUBE
    auth_method = AuthMethod.OAUTH2

    async def get_auth_url(self, state, **kw): return "url"
    async def handle_callback(self, code, state):
        return PlatformAccount(platform=self.platform, platform_uid="u", username="u", nickname="U"), OAuthCredential(access_token="t", refresh_token="r", expires_at=0)

    async def refresh_token(self, credential):
        return OAuthCredential(
            access_token="refreshed_token",
            refresh_token=credential.refresh_token,
            expires_at=int(time.time()) + 3600,
        )

    async def check_token_status(self, credential):
        return credential.expires_at > time.time()

    async def publish_video(self, cred, video_path, title, **kw):
        return PublishResult(success=True)


def _make_db_mock(credential_row=None):
    """创建 mock 数据库。"""
    db = MagicMock()
    db.get_oauth_credential.return_value = credential_row
    db.upsert_oauth_credential.return_value = None
    return db


class TestTokenManager:
    @pytest.mark.asyncio
    async def test_get_valid_token_from_db(self):
        """数据库有有效 token → 直接返回。"""
        future_ts = int(time.time()) + 3600
        db = _make_db_mock({
            "access_token": "valid_token",
            "refresh_token": "rt",
            "expires_at": future_ts,
            "refresh_expires_at": None,
            "raw": None,
        })
        tm = TokenManager(db)
        svc = MockPlatformService()

        cred = await tm.get_valid_token("acc_1", svc)
        assert cred.access_token == "valid_token"
        # 不应该调用 refresh
        db.upsert_oauth_credential.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_valid_token_triggers_refresh(self):
        """数据库 token 已过期 → 触发刷新并保存。"""
        expired_ts = int(time.time()) - 100
        db = _make_db_mock({
            "access_token": "expired_token",
            "refresh_token": "rt",
            "expires_at": expired_ts,
            "refresh_expires_at": None,
            "raw": None,
        })
        tm = TokenManager(db)
        svc = MockPlatformService()

        cred = await tm.get_valid_token("acc_1", svc)
        assert cred.access_token == "refreshed_token"
        # 应该保存到数据库
        db.upsert_oauth_credential.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_valid_token_no_credential_raises(self):
        """数据库无凭证 → 抛出 TokenExpiredError。"""
        db = _make_db_mock(None)
        tm = TokenManager(db)
        svc = MockPlatformService()

        with pytest.raises(TokenExpiredError):
            await tm.get_valid_token("acc_missing", svc)

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """第二次调用直接从缓存获取，不查数据库。"""
        future_ts = int(time.time()) + 3600
        db = _make_db_mock({
            "access_token": "cached",
            "refresh_token": "rt",
            "expires_at": future_ts,
            "refresh_expires_at": None,
            "raw": None,
        })
        tm = TokenManager(db)
        svc = MockPlatformService()

        await tm.get_valid_token("acc_1", svc)
        # 重置 mock 计数
        db.get_oauth_credential.reset_mock()

        await tm.get_valid_token("acc_1", svc)
        # 缓存命中 → 不应查数据库
        db.get_oauth_credential.assert_not_called()

    def test_invalidate(self):
        """invalidate 清除缓存。"""
        db = _make_db_mock()
        tm = TokenManager(db)
        # 手动放入缓存
        tm._cache["acc_1"] = OAuthCredential(
            access_token="t", refresh_token="r", expires_at=9999999999,
        )
        assert "acc_1" in tm._cache
        tm.invalidate("acc_1")
        assert "acc_1" not in tm._cache

    def test_cache_stats(self):
        """cache_stats 返回缓存元信息。"""
        db = _make_db_mock()
        tm = TokenManager(db)
        stats = tm.cache_stats()
        assert stats["maxsize"] == 1000
        assert stats["ttl"] == 1800
        assert stats["size"] == 0

    def test_save_credential(self):
        """save_credential 同时写缓存和数据库。"""
        db = _make_db_mock()
        tm = TokenManager(db)
        cred = OAuthCredential(
            access_token="at", refresh_token="rt", expires_at=9999999999,
        )
        tm.save_credential("acc_1", "youtube", cred)

        assert tm._cache.get("acc_1") is cred
        db.upsert_oauth_credential.assert_called_once()

    @pytest.mark.asyncio
    async def test_expired_no_refresh_token_raises(self):
        """token 过期且无 refresh_token → 抛出 TokenExpiredError。"""
        expired_ts = int(time.time()) - 100
        db = _make_db_mock({
            "access_token": "expired",
            "refresh_token": "",
            "expires_at": expired_ts,
            "refresh_expires_at": None,
            "raw": None,
        })
        tm = TokenManager(db)
        svc = MockPlatformService()

        with pytest.raises(TokenExpiredError, match="no refresh_token"):
            await tm.get_valid_token("acc_1", svc)
