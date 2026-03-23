"""
Sprint 1: PlatformRegistry 单元测试。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from platform_services.base import AuthMethod, OAuthCredential, PlatformAccount, PlatformService, PlatformType, PublishResult  # noqa: E402
from platform_services.registry import PlatformRegistry  # noqa: E402


class StubServiceA(PlatformService):
    platform = PlatformType.YOUTUBE
    auth_method = AuthMethod.OAUTH2

    async def get_auth_url(self, state, **kw): return "url_a"
    async def handle_callback(self, code, state):
        return PlatformAccount(platform=self.platform, platform_uid="u1", username="a", nickname="A"), OAuthCredential(access_token="t", refresh_token="r", expires_at=0)
    async def refresh_token(self, cred): return cred
    async def check_token_status(self, cred): return True
    async def publish_video(self, cred, video_path, title, **kw): return PublishResult(success=True)


class StubServiceB(PlatformService):
    platform = PlatformType.BILIBILI
    auth_method = AuthMethod.OAUTH2

    async def get_auth_url(self, state, **kw): return "url_b"
    async def handle_callback(self, code, state):
        return PlatformAccount(platform=self.platform, platform_uid="u2", username="b", nickname="B"), OAuthCredential(access_token="t", refresh_token="r", expires_at=0)
    async def refresh_token(self, cred): return cred
    async def check_token_status(self, cred): return True
    async def publish_video(self, cred, video_path, title, **kw): return PublishResult(success=True)


@pytest.fixture(autouse=True)
def clean_registry():
    PlatformRegistry.clear()
    yield
    PlatformRegistry.clear()


class TestPlatformRegistry:
    def test_register_and_get(self):
        svc = StubServiceA()
        PlatformRegistry.register(svc)
        assert PlatformRegistry.get("youtube") is svc
        assert PlatformRegistry.get("nonexistent") is None

    def test_list_platforms(self):
        PlatformRegistry.register(StubServiceA())
        PlatformRegistry.register(StubServiceB())
        platforms = PlatformRegistry.list_platforms()
        names = {p["platform"] for p in platforms}
        assert names == {"youtube", "bilibili"}

    def test_clear(self):
        PlatformRegistry.register(StubServiceA())
        assert len(PlatformRegistry.list_platforms()) == 1
        PlatformRegistry.clear()
        assert len(PlatformRegistry.list_platforms()) == 0

    def test_overwrite_same_platform(self):
        svc1 = StubServiceA()
        svc2 = StubServiceA()
        PlatformRegistry.register(svc1)
        PlatformRegistry.register(svc2)
        assert PlatformRegistry.get("youtube") is svc2
        assert len(PlatformRegistry.list_platforms()) == 1
