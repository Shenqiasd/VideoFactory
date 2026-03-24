"""
International Platform OAuth Service Unit Tests.

Comprehensive tests for 8 international platform OAuth service implementations:
YouTube, TikTok, Facebook, Instagram, Twitter, Pinterest, LinkedIn, Threads.

Covers: get_auth_url, handle_callback, refresh_token, check_token_status,
and service initialization. All HTTP calls are mocked.
"""

import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from platform_services.base import (  # noqa: E402
    AuthMethod,
    OAuthCredential,
    PlatformAccount,
    PlatformType,
)
from platform_services.exceptions import OAuthError  # noqa: E402
from platform_services.facebook import FacebookService  # noqa: E402
from platform_services.instagram import InstagramService  # noqa: E402
from platform_services.linkedin import LinkedInService  # noqa: E402
from platform_services.pinterest import PinterestService  # noqa: E402
from platform_services.threads import ThreadsService  # noqa: E402
from platform_services.tiktok import TikTokService  # noqa: E402
from platform_services.twitter import TwitterService, _pkce_store  # noqa: E402
from platform_services.youtube import YouTubeService  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_async_client(responses_by_method):
    """
    Build a patched httpx.AsyncClient context manager.

    responses_by_method: dict with keys "post", "get" mapping to callables
    that accept (url, **kwargs) and return a MagicMock response.
    """
    mock_client = AsyncMock()
    if "post" in responses_by_method:
        mock_client.post = responses_by_method["post"]
    if "get" in responses_by_method:
        mock_client.get = responses_by_method["get"]
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _make_response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


# ===================================================================
# YouTube Tests
# ===================================================================

class TestYouTubeService:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.service = YouTubeService(
            client_id="yt_client_id",
            client_secret="yt_client_secret",
            redirect_uri="http://localhost:9000/api/oauth/callback/youtube",
        )

    def test_platform_attributes(self):
        assert self.service.platform == PlatformType.YOUTUBE
        assert self.service.auth_method == AuthMethod.OAUTH2

    @pytest.mark.asyncio
    async def test_get_auth_url(self):
        url = await self.service.get_auth_url(state="state123")
        assert "accounts.google.com" in url
        assert "yt_client_id" in url
        assert "state123" in url
        assert "response_type=code" in url
        assert "access_type=offline" in url
        assert "prompt=consent" in url
        # scopes
        assert "youtube.upload" in url
        assert "youtube.readonly" in url
        assert "userinfo.profile" in url

    @pytest.mark.asyncio
    async def test_handle_callback_success(self):
        token_data = {
            "access_token": "ya29.access",
            "refresh_token": "1//refresh",
            "expires_in": 3600,
        }
        channel_data = {
            "items": [{
                "id": "UC_channel123",
                "snippet": {
                    "title": "My Channel",
                    "customUrl": "@mychannel",
                    "thumbnails": {"default": {"url": "https://img.yt/av.jpg"}},
                },
            }],
        }

        call_count = {"post": 0, "get": 0}

        async def mock_post(url, **kw):
            call_count["post"] += 1
            return _make_response(200, token_data)

        async def mock_get(url, **kw):
            call_count["get"] += 1
            return _make_response(200, channel_data)

        with patch("platform_services.youtube.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post, "get": mock_get})
            account, credential = await self.service.handle_callback("code1", "state1")

        assert account.platform == PlatformType.YOUTUBE
        assert account.platform_uid == "UC_channel123"
        assert account.username == "@mychannel"
        assert account.nickname == "My Channel"
        assert account.avatar_url == "https://img.yt/av.jpg"
        assert credential.access_token == "ya29.access"
        assert credential.refresh_token == "1//refresh"
        assert credential.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_handle_callback_token_exchange_failure(self):
        async def mock_post(url, **kw):
            return _make_response(400, text="invalid_grant")

        with patch("platform_services.youtube.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            with pytest.raises(OAuthError, match="token exchange failed"):
                await self.service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_handle_callback_no_channel(self):
        async def mock_post(url, **kw):
            return _make_response(200, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600})

        async def mock_get(url, **kw):
            return _make_response(200, {"items": []})

        with patch("platform_services.youtube.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post, "get": mock_get})
            with pytest.raises(OAuthError, match="未找到关联的频道"):
                await self.service.handle_callback("c", "s")

    @pytest.mark.asyncio
    async def test_refresh_token_success(self):
        cred = OAuthCredential(access_token="old", refresh_token="rt_orig", expires_at=int(time.time()) + 3600)

        async def mock_post(url, **kw):
            return _make_response(200, {"access_token": "ya29.new", "expires_in": 3600})

        with patch("platform_services.youtube.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            new_cred = await self.service.refresh_token(cred)

        assert new_cred.access_token == "ya29.new"
        assert new_cred.refresh_token == "rt_orig"  # Google preserves original refresh_token
        assert new_cred.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_refresh_token_failure(self):
        cred = OAuthCredential(access_token="old", refresh_token="rt", expires_at=int(time.time()) + 3600)

        async def mock_post(url, **kw):
            return _make_response(401, text="invalid_grant")

        with patch("platform_services.youtube.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            with pytest.raises(OAuthError, match="refresh failed"):
                await self.service.refresh_token(cred)

    @pytest.mark.asyncio
    async def test_check_token_status_valid(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) + 3600)
        assert await self.service.check_token_status(cred) is True

    @pytest.mark.asyncio
    async def test_check_token_status_expiring(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) + 300)
        assert await self.service.check_token_status(cred) is False

    @pytest.mark.asyncio
    async def test_check_token_status_expired(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) - 100)
        assert await self.service.check_token_status(cred) is False


# ===================================================================
# TikTok Tests
# ===================================================================

class TestTikTokService:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.service = TikTokService(
            client_id="tt_client_id",
            client_secret="tt_client_secret",
            redirect_uri="http://localhost:9000/api/oauth/callback/tiktok",
        )

    def test_platform_attributes(self):
        assert self.service.platform == PlatformType.TIKTOK
        assert self.service.auth_method == AuthMethod.OAUTH2

    @pytest.mark.asyncio
    async def test_get_auth_url(self):
        url = await self.service.get_auth_url(state="tt_state")
        assert "tiktok.com" in url
        # TikTok uses client_key instead of client_id in the URL
        assert "client_key=tt_client_id" in url
        assert "tt_state" in url
        assert "response_type=code" in url
        assert "user.info.basic" in url
        assert "video.publish" in url

    @pytest.mark.asyncio
    async def test_handle_callback_success(self):
        token_data = {
            "access_token": "tt_access",
            "refresh_token": "tt_refresh",
            "expires_in": 86400,
            "open_id": "open_123",
        }
        user_resp_data = {
            "data": {
                "user": {
                    "open_id": "open_123",
                    "display_name": "TikToker",
                    "avatar_url": "https://tiktok.com/av.jpg",
                },
            },
            "error": {"code": "ok"},
        }

        async def mock_post(url, **kw):
            return _make_response(200, token_data)

        async def mock_get(url, **kw):
            return _make_response(200, user_resp_data)

        with patch("platform_services.tiktok.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post, "get": mock_get})
            account, credential = await self.service.handle_callback("code", "state")

        assert account.platform == PlatformType.TIKTOK
        assert account.platform_uid == "open_123"
        assert account.nickname == "TikToker"
        assert account.avatar_url == "https://tiktok.com/av.jpg"
        assert credential.access_token == "tt_access"
        assert credential.refresh_token == "tt_refresh"

    @pytest.mark.asyncio
    async def test_handle_callback_token_exchange_failure(self):
        async def mock_post(url, **kw):
            return _make_response(400, text="bad request")

        with patch("platform_services.tiktok.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            with pytest.raises(OAuthError, match="TikTok token exchange failed"):
                await self.service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_handle_callback_token_error_in_body(self):
        """TikTok may return 200 but with an error field in the JSON body."""
        token_data = {
            "error": "invalid_grant",
            "error_description": "Code expired",
        }

        async def mock_post(url, **kw):
            return _make_response(200, token_data)

        with patch("platform_services.tiktok.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            with pytest.raises(OAuthError, match="token exchange error"):
                await self.service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_refresh_token_success(self):
        cred = OAuthCredential(access_token="old", refresh_token="tt_rt", expires_at=int(time.time()) + 3600)
        refresh_data = {
            "access_token": "tt_new_access",
            "refresh_token": "tt_new_refresh",
            "expires_in": 86400,
        }

        async def mock_post(url, **kw):
            return _make_response(200, refresh_data)

        with patch("platform_services.tiktok.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            new_cred = await self.service.refresh_token(cred)

        assert new_cred.access_token == "tt_new_access"
        assert new_cred.refresh_token == "tt_new_refresh"
        assert new_cred.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_refresh_token_failure(self):
        cred = OAuthCredential(access_token="old", refresh_token="rt", expires_at=int(time.time()) + 3600)

        async def mock_post(url, **kw):
            return _make_response(400, text="bad")

        with patch("platform_services.tiktok.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            with pytest.raises(OAuthError, match="TikTok token refresh failed"):
                await self.service.refresh_token(cred)

    @pytest.mark.asyncio
    async def test_refresh_token_error_in_body(self):
        cred = OAuthCredential(access_token="old", refresh_token="rt", expires_at=int(time.time()) + 3600)

        async def mock_post(url, **kw):
            return _make_response(200, {"error": "invalid_refresh", "error_description": "Expired"})

        with patch("platform_services.tiktok.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            with pytest.raises(OAuthError, match="TikTok token refresh error"):
                await self.service.refresh_token(cred)

    @pytest.mark.asyncio
    async def test_check_token_status_valid(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) + 3600)
        assert await self.service.check_token_status(cred) is True

    @pytest.mark.asyncio
    async def test_check_token_status_expiring(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) + 300)
        assert await self.service.check_token_status(cred) is False

    @pytest.mark.asyncio
    async def test_check_token_status_expired(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) - 100)
        assert await self.service.check_token_status(cred) is False


# ===================================================================
# Facebook Tests
# ===================================================================

class TestFacebookService:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.service = FacebookService(
            client_id="fb_client_id",
            client_secret="fb_client_secret",
            redirect_uri="http://localhost:9000/api/oauth/callback/facebook",
        )

    def test_platform_attributes(self):
        assert self.service.platform == PlatformType.FACEBOOK
        assert self.service.auth_method == AuthMethod.OAUTH2

    @pytest.mark.asyncio
    async def test_get_auth_url(self):
        url = await self.service.get_auth_url(state="fb_state")
        assert "facebook.com" in url
        assert "fb_client_id" in url
        assert "fb_state" in url
        assert "response_type=code" in url
        assert "pages_manage_posts" in url
        assert "publish_video" in url

    @pytest.mark.asyncio
    async def test_handle_callback_success(self):
        """Facebook: code -> short token -> long token -> me -> pages -> page avatar."""
        short_token_data = {"access_token": "short_at"}
        long_token_data = {"access_token": "long_at", "expires_in": 5184000}
        me_data = {"id": "user_123", "name": "Fb User"}
        pages_data = {
            "data": [{
                "id": "page_456",
                "name": "My Page",
                "access_token": "page_token_789",
            }],
        }
        pic_data = {"data": {"url": "https://fb.com/pic.jpg"}}

        get_call_idx = {"idx": 0}

        async def mock_get(url, **kw):
            get_call_idx["idx"] += 1
            idx = get_call_idx["idx"]
            if idx == 1:
                # _exchange_code_for_token (uses GET)
                return _make_response(200, short_token_data)
            elif idx == 2:
                # _exchange_long_lived_token (uses GET)
                return _make_response(200, long_token_data)
            elif idx == 3:
                # /me
                return _make_response(200, me_data)
            elif idx == 4:
                # /user_id/accounts (pages)
                return _make_response(200, pages_data)
            elif idx == 5:
                # /page_id/picture
                return _make_response(200, pic_data)
            return _make_response(200, {})

        with patch("platform_services.facebook.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"get": mock_get})
            account, credential = await self.service.handle_callback("code", "state")

        assert account.platform == PlatformType.FACEBOOK
        assert account.platform_uid == "page_456"
        assert account.nickname == "My Page"
        assert account.avatar_url == "https://fb.com/pic.jpg"
        assert credential.access_token == "page_token_789"
        assert credential.refresh_token == "long_at"
        raw = json.loads(credential.raw)
        assert raw["page_id"] == "page_456"
        assert raw["user_id"] == "user_123"

    @pytest.mark.asyncio
    async def test_handle_callback_no_pages(self):
        short_token_data = {"access_token": "short_at"}
        long_token_data = {"access_token": "long_at", "expires_in": 5184000}
        me_data = {"id": "user_123", "name": "Fb User"}
        pages_data = {"data": []}

        get_call_idx = {"idx": 0}

        async def mock_get(url, **kw):
            get_call_idx["idx"] += 1
            idx = get_call_idx["idx"]
            if idx == 1:
                return _make_response(200, short_token_data)
            elif idx == 2:
                return _make_response(200, long_token_data)
            elif idx == 3:
                return _make_response(200, me_data)
            elif idx == 4:
                return _make_response(200, pages_data)
            return _make_response(200, {})

        with patch("platform_services.facebook.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"get": mock_get})
            with pytest.raises(OAuthError, match="未找到关联的 Page"):
                await self.service.handle_callback("code", "state")

    @pytest.mark.asyncio
    async def test_refresh_token_success(self):
        """Facebook refresh: exchange long-lived user token -> get page token."""
        cred = OAuthCredential(
            access_token="old_page_token",
            refresh_token="old_user_token",
            expires_at=int(time.time()) + 3600,
            raw=json.dumps({"page_id": "page_456", "user_id": "user_123"}),
        )
        long_token_data = {"access_token": "new_user_token", "expires_in": 5184000}
        pages_data = {
            "data": [{
                "id": "page_456",
                "name": "My Page",
                "access_token": "new_page_token",
            }],
        }

        get_call_idx = {"idx": 0}

        async def mock_get(url, **kw):
            get_call_idx["idx"] += 1
            if get_call_idx["idx"] == 1:
                return _make_response(200, long_token_data)
            elif get_call_idx["idx"] == 2:
                return _make_response(200, pages_data)
            return _make_response(200, {})

        with patch("platform_services.facebook.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"get": mock_get})
            new_cred = await self.service.refresh_token(cred)

        assert new_cred.access_token == "new_page_token"
        assert new_cred.refresh_token == "new_user_token"
        assert new_cred.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_check_token_status_valid(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) + 3600)
        assert await self.service.check_token_status(cred) is True

    @pytest.mark.asyncio
    async def test_check_token_status_expiring(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) + 300)
        assert await self.service.check_token_status(cred) is False

    @pytest.mark.asyncio
    async def test_check_token_status_expired(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) - 100)
        assert await self.service.check_token_status(cred) is False


# ===================================================================
# Instagram Tests
# ===================================================================

class TestInstagramService:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.service = InstagramService(
            client_id="ig_client_id",
            client_secret="ig_client_secret",
            redirect_uri="http://localhost:9000/api/oauth/callback/instagram",
        )

    def test_platform_attributes(self):
        assert self.service.platform == PlatformType.INSTAGRAM
        assert self.service.auth_method == AuthMethod.OAUTH2

    @pytest.mark.asyncio
    async def test_get_auth_url(self):
        url = await self.service.get_auth_url(state="ig_state")
        # Instagram uses Meta's auth URL
        assert "facebook.com" in url
        assert "ig_client_id" in url
        assert "ig_state" in url
        assert "response_type=code" in url
        assert "instagram_basic" in url
        assert "instagram_content_publish" in url

    @pytest.mark.asyncio
    async def test_handle_callback_success(self):
        """Instagram: code -> short token -> long token -> me -> pages -> IG business -> IG info."""
        short_token_data = {"access_token": "short_at"}
        long_token_data = {"access_token": "long_at", "expires_in": 5184000}
        me_data = {"id": "user_111"}
        pages_data = {
            "data": [{
                "id": "page_222",
                "access_token": "page_at",
            }],
        }
        ig_business_data = {
            "instagram_business_account": {"id": "ig_333"},
        }
        ig_account_data = {
            "id": "ig_333",
            "username": "insta_user",
            "name": "Insta Name",
            "profile_picture_url": "https://ig.com/pic.jpg",
        }

        get_call_idx = {"idx": 0}

        async def mock_get(url, **kw):
            get_call_idx["idx"] += 1
            idx = get_call_idx["idx"]
            if idx == 1:
                return _make_response(200, short_token_data)
            elif idx == 2:
                return _make_response(200, long_token_data)
            elif idx == 3:
                return _make_response(200, me_data)
            elif idx == 4:
                return _make_response(200, pages_data)
            elif idx == 5:
                return _make_response(200, ig_business_data)
            elif idx == 6:
                return _make_response(200, ig_account_data)
            return _make_response(200, {})

        with patch("platform_services.instagram.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"get": mock_get})
            account, credential = await self.service.handle_callback("code", "state")

        assert account.platform == PlatformType.INSTAGRAM
        assert account.platform_uid == "ig_333"
        assert account.username == "insta_user"
        assert account.nickname == "Insta Name"
        assert account.avatar_url == "https://ig.com/pic.jpg"
        assert credential.access_token == "long_at"
        raw = json.loads(credential.raw)
        assert raw["ig_user_id"] == "ig_333"
        assert raw["page_id"] == "page_222"

    @pytest.mark.asyncio
    async def test_handle_callback_no_ig_business(self):
        """Should fail if the Page has no Instagram Business Account."""
        short_token_data = {"access_token": "short_at"}
        long_token_data = {"access_token": "long_at", "expires_in": 5184000}
        me_data = {"id": "user_111"}
        pages_data = {"data": [{"id": "page_222", "access_token": "page_at"}]}
        ig_no_business = {}  # no instagram_business_account key

        get_call_idx = {"idx": 0}

        async def mock_get(url, **kw):
            get_call_idx["idx"] += 1
            idx = get_call_idx["idx"]
            if idx == 1:
                return _make_response(200, short_token_data)
            elif idx == 2:
                return _make_response(200, long_token_data)
            elif idx == 3:
                return _make_response(200, me_data)
            elif idx == 4:
                return _make_response(200, pages_data)
            elif idx == 5:
                return _make_response(200, ig_no_business)
            return _make_response(200, {})

        with patch("platform_services.instagram.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"get": mock_get})
            with pytest.raises(OAuthError, match="未关联 Instagram Business"):
                await self.service.handle_callback("code", "state")

    @pytest.mark.asyncio
    async def test_handle_callback_no_pages(self):
        short_token_data = {"access_token": "short_at"}
        long_token_data = {"access_token": "long_at", "expires_in": 5184000}
        me_data = {"id": "user_111"}
        pages_data = {"data": []}

        get_call_idx = {"idx": 0}

        async def mock_get(url, **kw):
            get_call_idx["idx"] += 1
            idx = get_call_idx["idx"]
            if idx == 1:
                return _make_response(200, short_token_data)
            elif idx == 2:
                return _make_response(200, long_token_data)
            elif idx == 3:
                return _make_response(200, me_data)
            elif idx == 4:
                return _make_response(200, pages_data)
            return _make_response(200, {})

        with patch("platform_services.instagram.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"get": mock_get})
            with pytest.raises(OAuthError, match="未找到关联的 Facebook Page"):
                await self.service.handle_callback("code", "state")

    @pytest.mark.asyncio
    async def test_refresh_token_success(self):
        """Instagram uses Meta long-lived token exchange for refresh."""
        cred = OAuthCredential(
            access_token="old_long_at",
            refresh_token="old_long_at",
            expires_at=int(time.time()) + 3600,
            raw=json.dumps({"ig_user_id": "ig_333", "page_id": "page_222", "user_id": "u1"}),
        )

        async def mock_get(url, **kw):
            return _make_response(200, {"access_token": "new_long_at", "expires_in": 5184000})

        with patch("platform_services.instagram.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"get": mock_get})
            new_cred = await self.service.refresh_token(cred)

        assert new_cred.access_token == "new_long_at"
        assert new_cred.refresh_token == "new_long_at"
        assert new_cred.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_check_token_status_valid(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) + 3600)
        assert await self.service.check_token_status(cred) is True

    @pytest.mark.asyncio
    async def test_check_token_status_expiring(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) + 300)
        assert await self.service.check_token_status(cred) is False


# ===================================================================
# Twitter Tests
# ===================================================================

class TestTwitterService:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.service = TwitterService(
            client_id="tw_client_id",
            client_secret="tw_client_secret",
            redirect_uri="http://localhost:9000/api/oauth/callback/twitter",
        )

    def test_platform_attributes(self):
        assert self.service.platform == PlatformType.TWITTER
        assert self.service.auth_method == AuthMethod.OAUTH2

    @pytest.mark.asyncio
    async def test_get_auth_url_contains_pkce(self):
        url = await self.service.get_auth_url(state="tw_state_abc")
        assert "twitter.com" in url
        assert "tw_client_id" in url
        assert "tw_state_abc" in url
        assert "response_type=code" in url
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url
        # Verify scopes
        assert "tweet.read" in url
        assert "tweet.write" in url
        assert "offline.access" in url

    @pytest.mark.asyncio
    async def test_get_auth_url_stores_pkce_verifier(self):
        state = "pkce_test_state"
        await self.service.get_auth_url(state=state)
        assert state in _pkce_store
        verifier = _pkce_store[state]
        assert len(verifier) > 40  # code_verifier should be ~86 chars

    @pytest.mark.asyncio
    async def test_handle_callback_success(self):
        state = "tw_cb_state"
        # Pre-populate PKCE store
        _pkce_store[state] = "test_code_verifier_value"

        token_data = {
            "access_token": "tw_access",
            "refresh_token": "tw_refresh",
            "expires_in": 7200,
        }
        user_data = {
            "data": {
                "id": "tw_user_123",
                "username": "tweetuser",
                "name": "Tweet User",
                "profile_image_url": "https://tw.com/av.jpg",
            },
        }

        async def mock_post(url, **kw):
            return _make_response(200, token_data)

        async def mock_get(url, **kw):
            return _make_response(200, user_data)

        with patch("platform_services.twitter.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post, "get": mock_get})
            account, credential = await self.service.handle_callback("code", state)

        assert account.platform == PlatformType.TWITTER
        assert account.platform_uid == "tw_user_123"
        assert account.username == "tweetuser"
        assert account.nickname == "Tweet User"
        assert account.avatar_url == "https://tw.com/av.jpg"
        assert credential.access_token == "tw_access"
        assert credential.refresh_token == "tw_refresh"
        # Verify PKCE verifier was consumed
        assert state not in _pkce_store

    @pytest.mark.asyncio
    async def test_handle_callback_missing_pkce(self):
        """If no PKCE verifier is found for the state, should raise OAuthError."""
        with pytest.raises(OAuthError, match="PKCE code_verifier not found"):
            await self.service.handle_callback("code", "unknown_state")

    @pytest.mark.asyncio
    async def test_handle_callback_token_exchange_failure(self):
        state = "tw_fail_state"
        _pkce_store[state] = "verifier"

        async def mock_post(url, **kw):
            return _make_response(401, text="unauthorized")

        with patch("platform_services.twitter.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            with pytest.raises(OAuthError, match="Twitter token exchange failed"):
                await self.service.handle_callback("bad", state)

    @pytest.mark.asyncio
    async def test_refresh_token_success(self):
        cred = OAuthCredential(access_token="old", refresh_token="tw_rt", expires_at=int(time.time()) + 3600)

        async def mock_post(url, **kw):
            return _make_response(200, {
                "access_token": "tw_new_access",
                "refresh_token": "tw_new_refresh",
                "expires_in": 7200,
            })

        with patch("platform_services.twitter.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            new_cred = await self.service.refresh_token(cred)

        assert new_cred.access_token == "tw_new_access"
        assert new_cred.refresh_token == "tw_new_refresh"  # Twitter returns new refresh_token
        assert new_cred.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_refresh_token_failure(self):
        cred = OAuthCredential(access_token="old", refresh_token="rt", expires_at=int(time.time()) + 3600)

        async def mock_post(url, **kw):
            return _make_response(401, text="bad")

        with patch("platform_services.twitter.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            with pytest.raises(OAuthError, match="Twitter token refresh failed"):
                await self.service.refresh_token(cred)

    @pytest.mark.asyncio
    async def test_check_token_status_valid(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) + 3600)
        assert await self.service.check_token_status(cred) is True

    @pytest.mark.asyncio
    async def test_check_token_status_expiring(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) + 300)
        assert await self.service.check_token_status(cred) is False

    @pytest.mark.asyncio
    async def test_check_token_status_expired(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) - 100)
        assert await self.service.check_token_status(cred) is False


# ===================================================================
# Pinterest Tests
# ===================================================================

class TestPinterestService:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.service = PinterestService(
            client_id="pin_client_id",
            client_secret="pin_client_secret",
            redirect_uri="http://localhost:9000/api/oauth/callback/pinterest",
        )

    def test_platform_attributes(self):
        assert self.service.platform == PlatformType.PINTEREST
        assert self.service.auth_method == AuthMethod.OAUTH2

    @pytest.mark.asyncio
    async def test_get_auth_url(self):
        url = await self.service.get_auth_url(state="pin_state")
        assert "pinterest.com" in url
        assert "pin_client_id" in url
        assert "pin_state" in url
        assert "response_type=code" in url
        assert "boards%3Aread" in url or "boards:read" in url
        assert "pins%3Awrite" in url or "pins:write" in url

    @pytest.mark.asyncio
    async def test_handle_callback_success(self):
        token_data = {
            "access_token": "pin_access",
            "refresh_token": "pin_refresh",
            "expires_in": 3600,
        }
        user_data = {
            "username": "pinuser",
            "business_name": "Pin Business",
            "profile_image": "https://pin.com/av.jpg",
        }

        async def mock_post(url, **kw):
            return _make_response(200, token_data)

        async def mock_get(url, **kw):
            return _make_response(200, user_data)

        with patch("platform_services.pinterest.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post, "get": mock_get})
            account, credential = await self.service.handle_callback("code", "state")

        assert account.platform == PlatformType.PINTEREST
        assert account.platform_uid == "pinuser"
        assert account.username == "pinuser"
        assert account.nickname == "Pin Business"
        assert account.avatar_url == "https://pin.com/av.jpg"
        assert credential.access_token == "pin_access"
        assert credential.refresh_token == "pin_refresh"

    @pytest.mark.asyncio
    async def test_handle_callback_token_failure(self):
        async def mock_post(url, **kw):
            return _make_response(401, text="unauthorized")

        with patch("platform_services.pinterest.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            with pytest.raises(OAuthError, match="Pinterest token exchange failed"):
                await self.service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_handle_callback_user_info_failure(self):
        token_data = {"access_token": "pin_at", "refresh_token": "pin_rt", "expires_in": 3600}

        async def mock_post(url, **kw):
            return _make_response(200, token_data)

        async def mock_get(url, **kw):
            return _make_response(500, text="internal error")

        with patch("platform_services.pinterest.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post, "get": mock_get})
            with pytest.raises(OAuthError, match="Pinterest user info fetch failed"):
                await self.service.handle_callback("code", "state")

    @pytest.mark.asyncio
    async def test_refresh_token_success(self):
        cred = OAuthCredential(access_token="old", refresh_token="pin_rt", expires_at=int(time.time()) + 3600)

        async def mock_post(url, **kw):
            return _make_response(200, {
                "access_token": "pin_new_access",
                "refresh_token": "pin_new_refresh",
                "expires_in": 3600,
            })

        with patch("platform_services.pinterest.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            new_cred = await self.service.refresh_token(cred)

        assert new_cred.access_token == "pin_new_access"
        assert new_cred.refresh_token == "pin_new_refresh"

    @pytest.mark.asyncio
    async def test_refresh_token_failure(self):
        cred = OAuthCredential(access_token="old", refresh_token="rt", expires_at=int(time.time()) + 3600)

        async def mock_post(url, **kw):
            return _make_response(401, text="bad")

        with patch("platform_services.pinterest.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            with pytest.raises(OAuthError, match="Pinterest token refresh failed"):
                await self.service.refresh_token(cred)

    @pytest.mark.asyncio
    async def test_refresh_token_preserves_original_when_no_new_refresh(self):
        cred = OAuthCredential(access_token="old", refresh_token="pin_rt_orig", expires_at=int(time.time()) + 3600)

        async def mock_post(url, **kw):
            return _make_response(200, {"access_token": "new_at", "expires_in": 3600})

        with patch("platform_services.pinterest.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            new_cred = await self.service.refresh_token(cred)

        assert new_cred.refresh_token == "pin_rt_orig"

    @pytest.mark.asyncio
    async def test_check_token_status_valid(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) + 3600)
        assert await self.service.check_token_status(cred) is True

    @pytest.mark.asyncio
    async def test_check_token_status_expiring(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) + 300)
        assert await self.service.check_token_status(cred) is False

    @pytest.mark.asyncio
    async def test_check_token_status_expired(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) - 100)
        assert await self.service.check_token_status(cred) is False


# ===================================================================
# LinkedIn Tests
# ===================================================================

class TestLinkedInService:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.service = LinkedInService(
            client_id="li_client_id",
            client_secret="li_client_secret",
            redirect_uri="http://localhost:9000/api/oauth/callback/linkedin",
        )

    def test_platform_attributes(self):
        assert self.service.platform == PlatformType.LINKEDIN
        assert self.service.auth_method == AuthMethod.OAUTH2

    @pytest.mark.asyncio
    async def test_get_auth_url(self):
        url = await self.service.get_auth_url(state="li_state")
        assert "linkedin.com" in url
        assert "li_client_id" in url
        assert "li_state" in url
        assert "response_type=code" in url
        assert "openid" in url
        assert "profile" in url
        assert "w_member_social" in url

    @pytest.mark.asyncio
    async def test_handle_callback_success(self):
        token_data = {
            "access_token": "li_access",
            "refresh_token": "li_refresh",
            "expires_in": 3600,
        }
        user_data = {
            "sub": "li_user_456",
            "email": "user@linkedin.com",
            "name": "LinkedIn User",
            "picture": "https://li.com/pic.jpg",
        }

        async def mock_post(url, **kw):
            return _make_response(200, token_data)

        async def mock_get(url, **kw):
            return _make_response(200, user_data)

        with patch("platform_services.linkedin.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post, "get": mock_get})
            account, credential = await self.service.handle_callback("code", "state")

        assert account.platform == PlatformType.LINKEDIN
        assert account.platform_uid == "li_user_456"
        assert account.username == "user@linkedin.com"
        assert account.nickname == "LinkedIn User"
        assert account.avatar_url == "https://li.com/pic.jpg"
        assert credential.access_token == "li_access"
        assert credential.refresh_token == "li_refresh"

    @pytest.mark.asyncio
    async def test_handle_callback_token_failure(self):
        async def mock_post(url, **kw):
            return _make_response(400, text="bad request")

        with patch("platform_services.linkedin.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            with pytest.raises(OAuthError, match="LinkedIn token exchange failed"):
                await self.service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_handle_callback_user_info_failure(self):
        token_data = {"access_token": "li_at", "refresh_token": "li_rt", "expires_in": 3600}

        async def mock_post(url, **kw):
            return _make_response(200, token_data)

        async def mock_get(url, **kw):
            return _make_response(500, text="server error")

        with patch("platform_services.linkedin.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post, "get": mock_get})
            with pytest.raises(OAuthError, match="LinkedIn user info fetch failed"):
                await self.service.handle_callback("code", "state")

    @pytest.mark.asyncio
    async def test_refresh_token_success(self):
        cred = OAuthCredential(access_token="old", refresh_token="li_rt", expires_at=int(time.time()) + 3600)

        async def mock_post(url, **kw):
            return _make_response(200, {
                "access_token": "li_new_access",
                "refresh_token": "li_new_refresh",
                "expires_in": 3600,
            })

        with patch("platform_services.linkedin.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            new_cred = await self.service.refresh_token(cred)

        assert new_cred.access_token == "li_new_access"
        assert new_cred.refresh_token == "li_new_refresh"
        assert new_cred.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_refresh_token_failure(self):
        cred = OAuthCredential(access_token="old", refresh_token="rt", expires_at=int(time.time()) + 3600)

        async def mock_post(url, **kw):
            return _make_response(401, text="invalid")

        with patch("platform_services.linkedin.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            with pytest.raises(OAuthError, match="LinkedIn token refresh failed"):
                await self.service.refresh_token(cred)

    @pytest.mark.asyncio
    async def test_check_token_status_valid(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) + 3600)
        assert await self.service.check_token_status(cred) is True

    @pytest.mark.asyncio
    async def test_check_token_status_expiring(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) + 300)
        assert await self.service.check_token_status(cred) is False

    @pytest.mark.asyncio
    async def test_check_token_status_expired(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) - 100)
        assert await self.service.check_token_status(cred) is False


# ===================================================================
# Threads Tests
# ===================================================================

class TestThreadsService:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.service = ThreadsService(
            client_id="th_client_id",
            client_secret="th_client_secret",
            redirect_uri="http://localhost:9000/api/oauth/callback/threads",
        )

    def test_platform_attributes(self):
        assert self.service.platform == PlatformType.THREADS
        assert self.service.auth_method == AuthMethod.OAUTH2

    @pytest.mark.asyncio
    async def test_get_auth_url(self):
        url = await self.service.get_auth_url(state="th_state")
        assert "threads.net" in url
        assert "th_client_id" in url
        assert "th_state" in url
        assert "response_type=code" in url
        assert "threads_basic" in url
        assert "threads_content_publish" in url

    @pytest.mark.asyncio
    async def test_handle_callback_success(self):
        """Threads: code -> short token -> long token -> user info."""
        short_token_data = {"access_token": "th_short", "user_id": 123456}
        long_token_data = {"access_token": "th_long", "expires_in": 5184000}
        me_data = {
            "id": "th_user_789",
            "username": "threaduser",
            "name": "Thread User",
            "threads_profile_picture_url": "https://threads.com/pic.jpg",
        }

        post_call_idx = {"idx": 0}
        get_call_idx = {"idx": 0}

        async def mock_post(url, **kw):
            post_call_idx["idx"] += 1
            return _make_response(200, short_token_data)

        async def mock_get(url, **kw):
            get_call_idx["idx"] += 1
            if get_call_idx["idx"] == 1:
                return _make_response(200, long_token_data)
            elif get_call_idx["idx"] == 2:
                return _make_response(200, me_data)
            return _make_response(200, {})

        with patch("platform_services.threads.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post, "get": mock_get})
            account, credential = await self.service.handle_callback("code", "state")

        assert account.platform == PlatformType.THREADS
        assert account.platform_uid == "th_user_789"
        assert account.username == "threaduser"
        assert account.nickname == "Thread User"
        assert account.avatar_url == "https://threads.com/pic.jpg"
        assert credential.access_token == "th_long"
        assert credential.refresh_token == "th_long"  # Threads uses token exchange
        raw = json.loads(credential.raw)
        assert raw["user_id"] == "123456"

    @pytest.mark.asyncio
    async def test_handle_callback_token_exchange_failure(self):
        async def mock_post(url, **kw):
            return _make_response(400, text="bad request")

        with patch("platform_services.threads.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            with pytest.raises(OAuthError, match="Threads token exchange failed"):
                await self.service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_handle_callback_error_in_token_body(self):
        error_data = {"error": {"message": "Invalid code"}}

        async def mock_post(url, **kw):
            return _make_response(200, error_data)

        with patch("platform_services.threads.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post})
            with pytest.raises(OAuthError, match="Threads token exchange error"):
                await self.service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_handle_callback_long_lived_failure(self):
        short_token_data = {"access_token": "th_short", "user_id": 123}

        async def mock_post(url, **kw):
            return _make_response(200, short_token_data)

        async def mock_get(url, **kw):
            return _make_response(400, text="bad")

        with patch("platform_services.threads.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"post": mock_post, "get": mock_get})
            with pytest.raises(OAuthError, match="Threads long-lived token exchange failed"):
                await self.service.handle_callback("code", "state")

    @pytest.mark.asyncio
    async def test_refresh_token_success(self):
        """Threads refreshes by exchanging the existing long-lived token."""
        cred = OAuthCredential(
            access_token="th_old_token",
            refresh_token="th_old_token",
            expires_at=int(time.time()) + 3600,
            raw=json.dumps({"user_id": "123"}),
        )

        async def mock_get(url, **kw):
            return _make_response(200, {"access_token": "th_new_token", "expires_in": 5184000})

        with patch("platform_services.threads.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"get": mock_get})
            new_cred = await self.service.refresh_token(cred)

        assert new_cred.access_token == "th_new_token"
        assert new_cred.refresh_token == "th_new_token"
        assert new_cred.expires_at > int(time.time())
        # raw should be preserved
        assert new_cred.raw == cred.raw

    @pytest.mark.asyncio
    async def test_refresh_token_failure(self):
        cred = OAuthCredential(access_token="old", refresh_token="old", expires_at=int(time.time()) + 3600)

        async def mock_get(url, **kw):
            return _make_response(401, text="expired")

        with patch("platform_services.threads.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"get": mock_get})
            with pytest.raises(OAuthError, match="Threads token refresh failed"):
                await self.service.refresh_token(cred)

    @pytest.mark.asyncio
    async def test_refresh_token_error_in_body(self):
        cred = OAuthCredential(access_token="old", refresh_token="old", expires_at=int(time.time()) + 3600)

        async def mock_get(url, **kw):
            return _make_response(200, {"error": {"message": "Token expired"}})

        with patch("platform_services.threads.httpx.AsyncClient") as MC:
            MC.return_value = _mock_async_client({"get": mock_get})
            with pytest.raises(OAuthError, match="Threads token refresh error"):
                await self.service.refresh_token(cred)

    @pytest.mark.asyncio
    async def test_check_token_status_valid(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) + 3600)
        assert await self.service.check_token_status(cred) is True

    @pytest.mark.asyncio
    async def test_check_token_status_expiring(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) + 300)
        assert await self.service.check_token_status(cred) is False

    @pytest.mark.asyncio
    async def test_check_token_status_expired(self):
        cred = OAuthCredential(access_token="t", refresh_token="r", expires_at=int(time.time()) - 100)
        assert await self.service.check_token_status(cred) is False
