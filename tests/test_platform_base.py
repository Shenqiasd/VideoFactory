"""
Sprint 1: 平台抽象层单元测试。

覆盖 PlatformType / AuthMethod 枚举、数据类、PlatformService ABC。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from platform_services.base import (  # noqa: E402
    AuthMethod,
    OAuthCredential,
    PlatformAccount,
    PlatformService,
    PlatformType,
    PublishResult,
)


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

class TestPlatformType:
    def test_values(self):
        assert PlatformType.YOUTUBE.value == "youtube"
        assert PlatformType.BILIBILI.value == "bilibili"
        assert PlatformType.TIKTOK.value == "tiktok"

    def test_string_identity(self):
        assert PlatformType.YOUTUBE == "youtube"
        assert PlatformType("bilibili") == PlatformType.BILIBILI


class TestAuthMethod:
    def test_values(self):
        assert AuthMethod.OAUTH2.value == "oauth2"
        assert AuthMethod.COOKIE.value == "cookie"


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

class TestOAuthCredential:
    def test_required_fields(self):
        cred = OAuthCredential(
            access_token="at",
            refresh_token="rt",
            expires_at=1700000000,
        )
        assert cred.access_token == "at"
        assert cred.refresh_token == "rt"
        assert cred.expires_at == 1700000000
        assert cred.refresh_expires_at is None
        assert cred.raw is None

    def test_optional_fields(self):
        cred = OAuthCredential(
            access_token="at",
            refresh_token="rt",
            expires_at=1700000000,
            refresh_expires_at=1800000000,
            raw='{"scope":"read"}',
        )
        assert cred.refresh_expires_at == 1800000000
        assert cred.raw == '{"scope":"read"}'


class TestPlatformAccount:
    def test_fields(self):
        acc = PlatformAccount(
            platform=PlatformType.YOUTUBE,
            platform_uid="UC123",
            username="testuser",
            nickname="Test User",
            avatar_url="https://example.com/avatar.png",
        )
        assert acc.platform == PlatformType.YOUTUBE
        assert acc.platform_uid == "UC123"
        assert acc.avatar_url == "https://example.com/avatar.png"


class TestPublishResult:
    def test_success(self):
        r = PublishResult(success=True, post_id="123", permalink="https://youtube.com/watch?v=123")
        assert r.success is True
        assert r.status == "published"

    def test_failure(self):
        r = PublishResult(success=False, error="upload timeout")
        assert r.success is False
        assert r.error == "upload timeout"


# ---------------------------------------------------------------------------
# PlatformService ABC
# ---------------------------------------------------------------------------

class DummyService(PlatformService):
    """最小实现用于测试 ABC 约束。"""
    platform = PlatformType.YOUTUBE
    auth_method = AuthMethod.OAUTH2

    async def get_auth_url(self, state, **kwargs):
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}"

    async def handle_callback(self, code, state):
        return (
            PlatformAccount(
                platform=PlatformType.YOUTUBE,
                platform_uid="UC123",
                username="test",
                nickname="Test",
            ),
            OAuthCredential(access_token="at", refresh_token="rt", expires_at=9999999999),
        )

    async def refresh_token(self, credential):
        return OAuthCredential(
            access_token="new_at",
            refresh_token=credential.refresh_token,
            expires_at=9999999999,
        )

    async def check_token_status(self, credential):
        return credential.expires_at > 0

    async def publish_video(self, credential, video_path, title, description="", tags=None, cover_path="", **opts):
        return PublishResult(success=True, post_id="v_123")


class TestPlatformServiceABC:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            PlatformService()  # type: ignore

    @pytest.mark.asyncio
    async def test_dummy_get_auth_url(self):
        svc = DummyService()
        url = await svc.get_auth_url(state="abc")
        assert "state=abc" in url

    @pytest.mark.asyncio
    async def test_dummy_handle_callback(self):
        svc = DummyService()
        account, cred = await svc.handle_callback(code="code123", state="abc")
        assert account.platform_uid == "UC123"
        assert cred.access_token == "at"

    @pytest.mark.asyncio
    async def test_dummy_publish_video(self):
        svc = DummyService()
        cred = OAuthCredential(access_token="at", refresh_token="rt", expires_at=9999999999)
        result = await svc.publish_video(cred, "/tmp/video.mp4", "Test Video")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_optional_methods_raise_not_implemented(self):
        svc = DummyService()
        cred = OAuthCredential(access_token="at", refresh_token="rt", expires_at=9999999999)
        with pytest.raises(NotImplementedError):
            await svc.get_account_info(cred)
        with pytest.raises(NotImplementedError):
            await svc.delete_post(cred, "123")
        with pytest.raises(NotImplementedError):
            await svc.get_video_stats(cred, "123")
