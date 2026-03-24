"""
Chinese/Domestic Platform OAuth 单元测试。

覆盖 6 个国内平台的 OAuth 流程：
  - Douyin (抖音)
  - Bilibili (B站)
  - Kwai/Kuaishou (快手)
  - Xiaohongshu (小红书)
  - Weixin Channels / SPH (微信视频号)
  - Weixin GZH (微信公众号)

每个平台测试：get_auth_url, handle_callback, refresh_token, check_token_status,
以及 service 初始化属性。所有 HTTP 调用均使用 mock。
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

# --- Platform imports ---
from platform_services.douyin import (  # noqa: E402
    AUTH_URI as DOUYIN_AUTH_URI,
    TOKEN_URI as DOUYIN_TOKEN_URI,
    DouyinService,
)
from platform_services.bilibili import (  # noqa: E402
    AUTH_URI as BILIBILI_AUTH_URI,
    TOKEN_URI as BILIBILI_TOKEN_URI,
    REFRESH_URI as BILIBILI_REFRESH_URI,
    BilibiliService,
)
from platform_services.kwai import (  # noqa: E402
    AUTH_URI as KWAI_AUTH_URI,
    TOKEN_URI as KWAI_TOKEN_URI,
    REFRESH_URI as KWAI_REFRESH_URI,
    KwaiService,
)
from platform_services.xiaohongshu import (  # noqa: E402
    AUTH_URI as XHS_AUTH_URI,
    TOKEN_URI as XHS_TOKEN_URI,
    REFRESH_URI as XHS_REFRESH_URI,
    XiaohongshuService,
)
from platform_services.weixin_channels import (  # noqa: E402
    AUTH_URI as WEIXIN_SPH_AUTH_URI,
    TOKEN_URI as WEIXIN_SPH_TOKEN_URI,
    REFRESH_URI as WEIXIN_SPH_REFRESH_URI,
    WeixinChannelsService,
)
from platform_services.weixin_gzh import (  # noqa: E402
    AUTH_URI as WEIXIN_GZH_AUTH_URI,
    TOKEN_URI as WEIXIN_GZH_TOKEN_URI,
    REFRESH_URI as WEIXIN_GZH_REFRESH_URI,
    WeixinGzhService,
)


# ============================================================================
# Helper: mock httpx.AsyncClient context manager
# ============================================================================

def _build_mock_client(mock_post=None, mock_get=None):
    """Build a mock httpx.AsyncClient with __aenter__/__aexit__."""
    mock_client = AsyncMock()
    if mock_post is not None:
        mock_client.post = mock_post
    if mock_get is not None:
        mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _mock_resp(status_code=200, json_data=None, text=""):
    """Create a MagicMock response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


# ============================================================================
# Shared fixtures
# ============================================================================

@pytest.fixture
def valid_credential():
    return OAuthCredential(
        access_token="test_access_token",
        refresh_token="test_refresh_token",
        expires_at=int(time.time()) + 86400,
    )


@pytest.fixture
def expiring_credential():
    return OAuthCredential(
        access_token="expiring_token",
        refresh_token="test_refresh_token",
        expires_at=int(time.time()) + 300,  # within 600s buffer
    )


@pytest.fixture
def expired_credential():
    return OAuthCredential(
        access_token="expired_token",
        refresh_token="test_refresh_token",
        expires_at=int(time.time()) - 100,
    )


# ############################################################################
#
#  1. DOUYIN (抖音)
#
# ############################################################################

@pytest.fixture
def douyin_service():
    return DouyinService(
        client_key="dy_test_key",
        client_secret="dy_test_secret",
        redirect_uri="http://localhost:9000/api/oauth/callback/douyin",
    )


class TestDouyinAttributes:
    def test_platform(self, douyin_service):
        assert douyin_service.platform == PlatformType.DOUYIN

    def test_auth_method(self, douyin_service):
        assert douyin_service.auth_method == AuthMethod.OAUTH2

    def test_client_config(self, douyin_service):
        assert douyin_service.client_key == "dy_test_key"
        assert douyin_service.client_secret == "dy_test_secret"
        assert "douyin" in douyin_service.redirect_uri


class TestDouyinGetAuthUrl:
    @pytest.mark.asyncio
    async def test_url_contains_endpoint(self, douyin_service):
        url = await douyin_service.get_auth_url(state="st1")
        assert url.startswith(DOUYIN_AUTH_URI)

    @pytest.mark.asyncio
    async def test_url_contains_client_key(self, douyin_service):
        url = await douyin_service.get_auth_url(state="st1")
        assert "client_key=dy_test_key" in url

    @pytest.mark.asyncio
    async def test_url_contains_state(self, douyin_service):
        url = await douyin_service.get_auth_url(state="my_state_abc")
        assert "my_state_abc" in url

    @pytest.mark.asyncio
    async def test_url_contains_scopes(self, douyin_service):
        url = await douyin_service.get_auth_url(state="s")
        assert "user_info" in url
        assert "video.create" in url

    @pytest.mark.asyncio
    async def test_url_contains_redirect_uri(self, douyin_service):
        url = await douyin_service.get_auth_url(state="s")
        assert "redirect_uri=" in url


class TestDouyinHandleCallback:
    @pytest.mark.asyncio
    async def test_successful_callback(self, douyin_service):
        token_json = {
            "data": {
                "access_token": "dy_at_new",
                "refresh_token": "dy_rt_new",
                "expires_in": 86400,
                "open_id": "dy_uid_123",
                "error_code": 0,
                "description": "",
            },
            "extra": {"logid": "log1"},
        }
        user_json = {
            "data": {
                "open_id": "dy_uid_123",
                "nickname": "抖音用户",
                "avatar": "https://img.douyin.com/avatar.jpg",
                "error_code": 0,
                "description": "",
            },
            "extra": {"logid": "log2"},
        }

        async def mock_post(url, **kw):
            return _mock_resp(200, token_json)

        async def mock_get(url, **kw):
            return _mock_resp(200, user_json)

        with patch("platform_services.douyin.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post, mock_get)
            account, cred = await douyin_service.handle_callback("code1", "state1")

        assert account.platform == PlatformType.DOUYIN
        assert account.platform_uid == "dy_uid_123"
        assert account.nickname == "抖音用户"
        assert account.avatar_url == "https://img.douyin.com/avatar.jpg"
        assert cred.access_token == "dy_at_new"
        assert cred.refresh_token == "dy_rt_new"
        assert cred.expires_at > int(time.time())
        assert cred.raw is not None

    @pytest.mark.asyncio
    async def test_token_exchange_http_failure(self, douyin_service):
        async def mock_post(url, **kw):
            return _mock_resp(400, text="Bad Request")

        with patch("platform_services.douyin.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            with pytest.raises(OAuthError, match="token exchange failed"):
                await douyin_service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_token_exchange_api_error(self, douyin_service):
        async def mock_post(url, **kw):
            return _mock_resp(200, {
                "data": {"error_code": 10008, "description": "Invalid code"},
                "extra": {},
            })

        with patch("platform_services.douyin.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            with pytest.raises(OAuthError, match="token exchange error"):
                await douyin_service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_user_info_http_failure(self, douyin_service):
        async def mock_post(url, **kw):
            return _mock_resp(200, {
                "data": {
                    "access_token": "at", "refresh_token": "rt",
                    "expires_in": 86400, "open_id": "uid", "error_code": 0,
                },
            })

        async def mock_get(url, **kw):
            return _mock_resp(500, text="Server Error")

        with patch("platform_services.douyin.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post, mock_get)
            with pytest.raises(OAuthError, match="user info fetch failed"):
                await douyin_service.handle_callback("c", "s")

    @pytest.mark.asyncio
    async def test_user_info_api_error(self, douyin_service):
        async def mock_post(url, **kw):
            return _mock_resp(200, {
                "data": {
                    "access_token": "at", "refresh_token": "rt",
                    "expires_in": 86400, "open_id": "uid", "error_code": 0,
                },
            })

        async def mock_get(url, **kw):
            return _mock_resp(200, {
                "data": {"error_code": 10002, "description": "Token expired"},
            })

        with patch("platform_services.douyin.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post, mock_get)
            with pytest.raises(OAuthError, match="user info error"):
                await douyin_service.handle_callback("c", "s")


class TestDouyinRefreshToken:
    @pytest.mark.asyncio
    async def test_successful_refresh(self, douyin_service, valid_credential):
        async def mock_post(url, **kw):
            return _mock_resp(200, {
                "data": {
                    "access_token": "dy_at_refreshed",
                    "refresh_token": "dy_rt_refreshed",
                    "expires_in": 86400,
                    "error_code": 0,
                },
                "extra": {},
            })

        with patch("platform_services.douyin.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            new_cred = await douyin_service.refresh_token(valid_credential)

        assert new_cred.access_token == "dy_at_refreshed"
        assert new_cred.refresh_token == "dy_rt_refreshed"
        assert new_cred.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_refresh_http_failure(self, douyin_service, valid_credential):
        async def mock_post(url, **kw):
            return _mock_resp(401, text="Unauthorized")

        with patch("platform_services.douyin.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            with pytest.raises(OAuthError, match="refresh failed"):
                await douyin_service.refresh_token(valid_credential)

    @pytest.mark.asyncio
    async def test_refresh_api_error(self, douyin_service, valid_credential):
        async def mock_post(url, **kw):
            return _mock_resp(200, {
                "data": {"error_code": 10010, "description": "Refresh expired"},
                "extra": {},
            })

        with patch("platform_services.douyin.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            with pytest.raises(OAuthError, match="refresh error"):
                await douyin_service.refresh_token(valid_credential)


class TestDouyinCheckTokenStatus:
    @pytest.mark.asyncio
    async def test_valid_token(self, douyin_service, valid_credential):
        assert await douyin_service.check_token_status(valid_credential) is True

    @pytest.mark.asyncio
    async def test_expiring_token(self, douyin_service, expiring_credential):
        assert await douyin_service.check_token_status(expiring_credential) is False

    @pytest.mark.asyncio
    async def test_expired_token(self, douyin_service, expired_credential):
        assert await douyin_service.check_token_status(expired_credential) is False


# ############################################################################
#
#  2. BILIBILI (B站)
#
# ############################################################################

@pytest.fixture
def bilibili_service():
    return BilibiliService(
        client_id="bili_test_id",
        client_secret="bili_test_secret",
        redirect_uri="http://localhost:9000/api/oauth/callback/bilibili",
    )


class TestBilibiliAttributes:
    def test_platform(self, bilibili_service):
        assert bilibili_service.platform == PlatformType.BILIBILI

    def test_auth_method(self, bilibili_service):
        assert bilibili_service.auth_method == AuthMethod.OAUTH2

    def test_client_config(self, bilibili_service):
        assert bilibili_service.client_id == "bili_test_id"
        assert bilibili_service.client_secret == "bili_test_secret"


class TestBilibiliGetAuthUrl:
    @pytest.mark.asyncio
    async def test_url_contains_endpoint(self, bilibili_service):
        url = await bilibili_service.get_auth_url(state="st1")
        assert url.startswith(BILIBILI_AUTH_URI)

    @pytest.mark.asyncio
    async def test_url_contains_client_id(self, bilibili_service):
        url = await bilibili_service.get_auth_url(state="st1")
        assert "client_id=bili_test_id" in url

    @pytest.mark.asyncio
    async def test_url_contains_state(self, bilibili_service):
        url = await bilibili_service.get_auth_url(state="bili_state_xyz")
        assert "bili_state_xyz" in url

    @pytest.mark.asyncio
    async def test_url_contains_redirect_uri(self, bilibili_service):
        url = await bilibili_service.get_auth_url(state="s")
        assert "redirect_uri=" in url

    @pytest.mark.asyncio
    async def test_url_response_type(self, bilibili_service):
        url = await bilibili_service.get_auth_url(state="s")
        assert "response_type=code" in url


class TestBilibiliHandleCallback:
    @pytest.mark.asyncio
    async def test_successful_callback(self, bilibili_service):
        token_json = {
            "code": 0,
            "data": {
                "access_token": "bili_at_new",
                "refresh_token": "bili_rt_new",
                "expires_in": 86400,
            },
        }
        user_json = {
            "code": 0,
            "data": {
                "mid": 99887766,
                "name": "B站用户",
                "face": "https://i0.hdslb.com/bfs/face/test.jpg",
            },
        }

        async def mock_post(url, **kw):
            return _mock_resp(200, token_json)

        async def mock_get(url, **kw):
            return _mock_resp(200, user_json)

        with patch("platform_services.bilibili.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post, mock_get)
            account, cred = await bilibili_service.handle_callback("code1", "state1")

        assert account.platform == PlatformType.BILIBILI
        assert account.platform_uid == "99887766"
        assert account.nickname == "B站用户"
        assert account.avatar_url == "https://i0.hdslb.com/bfs/face/test.jpg"
        assert account.username == "99887766"
        assert cred.access_token == "bili_at_new"
        assert cred.refresh_token == "bili_rt_new"

    @pytest.mark.asyncio
    async def test_token_exchange_http_failure(self, bilibili_service):
        async def mock_post(url, **kw):
            return _mock_resp(500, text="Server Error")

        with patch("platform_services.bilibili.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            with pytest.raises(OAuthError, match="token exchange failed"):
                await bilibili_service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_token_exchange_api_error(self, bilibili_service):
        async def mock_post(url, **kw):
            return _mock_resp(200, {"code": -101, "message": "invalid code"})

        with patch("platform_services.bilibili.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            with pytest.raises(OAuthError, match="token exchange error"):
                await bilibili_service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_user_info_http_failure(self, bilibili_service):
        async def mock_post(url, **kw):
            return _mock_resp(200, {
                "code": 0,
                "data": {
                    "access_token": "at", "refresh_token": "rt", "expires_in": 86400,
                },
            })

        async def mock_get(url, **kw):
            return _mock_resp(500, text="Error")

        with patch("platform_services.bilibili.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post, mock_get)
            with pytest.raises(OAuthError, match="user info fetch failed"):
                await bilibili_service.handle_callback("c", "s")

    @pytest.mark.asyncio
    async def test_user_info_api_error(self, bilibili_service):
        async def mock_post(url, **kw):
            return _mock_resp(200, {
                "code": 0,
                "data": {
                    "access_token": "at", "refresh_token": "rt", "expires_in": 86400,
                },
            })

        async def mock_get(url, **kw):
            return _mock_resp(200, {"code": -101, "message": "unauthorized"})

        with patch("platform_services.bilibili.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post, mock_get)
            with pytest.raises(OAuthError, match="user info error"):
                await bilibili_service.handle_callback("c", "s")


class TestBilibiliRefreshToken:
    @pytest.mark.asyncio
    async def test_successful_refresh(self, bilibili_service, valid_credential):
        async def mock_post(url, **kw):
            return _mock_resp(200, {
                "code": 0,
                "data": {
                    "access_token": "bili_at_refreshed",
                    "refresh_token": "bili_rt_refreshed",
                    "expires_in": 86400,
                },
            })

        with patch("platform_services.bilibili.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            new_cred = await bilibili_service.refresh_token(valid_credential)

        assert new_cred.access_token == "bili_at_refreshed"
        assert new_cred.refresh_token == "bili_rt_refreshed"
        assert new_cred.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_refresh_http_failure(self, bilibili_service, valid_credential):
        async def mock_post(url, **kw):
            return _mock_resp(500, text="Error")

        with patch("platform_services.bilibili.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            with pytest.raises(OAuthError, match="refresh failed"):
                await bilibili_service.refresh_token(valid_credential)

    @pytest.mark.asyncio
    async def test_refresh_api_error(self, bilibili_service, valid_credential):
        async def mock_post(url, **kw):
            return _mock_resp(200, {"code": -101, "message": "refresh expired"})

        with patch("platform_services.bilibili.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            with pytest.raises(OAuthError, match="refresh error"):
                await bilibili_service.refresh_token(valid_credential)


class TestBilibiliCheckTokenStatus:
    @pytest.mark.asyncio
    async def test_valid_token(self, bilibili_service, valid_credential):
        assert await bilibili_service.check_token_status(valid_credential) is True

    @pytest.mark.asyncio
    async def test_expiring_token(self, bilibili_service, expiring_credential):
        assert await bilibili_service.check_token_status(expiring_credential) is False

    @pytest.mark.asyncio
    async def test_expired_token(self, bilibili_service, expired_credential):
        assert await bilibili_service.check_token_status(expired_credential) is False


# ############################################################################
#
#  3. KWAI / KUAISHOU (快手)
#
# ############################################################################

@pytest.fixture
def kwai_service():
    return KwaiService(
        client_id="kwai_test_id",
        client_secret="kwai_test_secret",
        redirect_uri="http://localhost:9000/api/oauth/callback/kwai",
    )


class TestKwaiAttributes:
    def test_platform(self, kwai_service):
        assert kwai_service.platform == PlatformType.KWAI

    def test_auth_method(self, kwai_service):
        assert kwai_service.auth_method == AuthMethod.OAUTH2

    def test_client_config(self, kwai_service):
        assert kwai_service.client_id == "kwai_test_id"
        assert kwai_service.client_secret == "kwai_test_secret"


class TestKwaiGetAuthUrl:
    @pytest.mark.asyncio
    async def test_url_contains_endpoint(self, kwai_service):
        url = await kwai_service.get_auth_url(state="st1")
        assert url.startswith(KWAI_AUTH_URI)

    @pytest.mark.asyncio
    async def test_url_contains_app_id(self, kwai_service):
        """Kwai uses app_id instead of client_id in the URL."""
        url = await kwai_service.get_auth_url(state="st1")
        assert "app_id=kwai_test_id" in url

    @pytest.mark.asyncio
    async def test_url_contains_state(self, kwai_service):
        url = await kwai_service.get_auth_url(state="kwai_state_123")
        assert "kwai_state_123" in url

    @pytest.mark.asyncio
    async def test_url_contains_scopes(self, kwai_service):
        url = await kwai_service.get_auth_url(state="s")
        assert "user_info" in url
        assert "video_publish" in url

    @pytest.mark.asyncio
    async def test_url_contains_redirect_uri(self, kwai_service):
        url = await kwai_service.get_auth_url(state="s")
        assert "redirect_uri=" in url


class TestKwaiHandleCallback:
    @pytest.mark.asyncio
    async def test_successful_callback(self, kwai_service):
        token_json = {
            "result": 1,
            "access_token": "kwai_at_new",
            "refresh_token": "kwai_rt_new",
            "expires_in": 86400,
            "open_id": "kwai_uid_456",
        }
        user_json = {
            "result": 1,
            "user_info": {
                "name": "快手达人",
                "head": "https://tx2.a]kuaishoucdn.com/avatar.jpg",
            },
        }

        async def mock_post(url, **kw):
            return _mock_resp(200, token_json)

        async def mock_get(url, **kw):
            return _mock_resp(200, user_json)

        with patch("platform_services.kwai.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post, mock_get)
            account, cred = await kwai_service.handle_callback("code1", "state1")

        assert account.platform == PlatformType.KWAI
        assert account.platform_uid == "kwai_uid_456"
        assert account.nickname == "快手达人"
        assert account.username == "快手达人"
        assert cred.access_token == "kwai_at_new"
        assert cred.refresh_token == "kwai_rt_new"
        assert cred.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_token_exchange_http_failure(self, kwai_service):
        async def mock_post(url, **kw):
            return _mock_resp(400, text="Bad Request")

        with patch("platform_services.kwai.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            with pytest.raises(OAuthError, match="token exchange failed"):
                await kwai_service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_token_exchange_api_error(self, kwai_service):
        """Kwai uses result != 1 for errors."""
        async def mock_post(url, **kw):
            return _mock_resp(200, {
                "result": 0,
                "error_msg": "invalid authorization code",
            })

        with patch("platform_services.kwai.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            with pytest.raises(OAuthError, match="token exchange error"):
                await kwai_service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_user_info_http_failure(self, kwai_service):
        async def mock_post(url, **kw):
            return _mock_resp(200, {
                "result": 1,
                "access_token": "at", "refresh_token": "rt",
                "expires_in": 86400, "open_id": "uid",
            })

        async def mock_get(url, **kw):
            return _mock_resp(500, text="Error")

        with patch("platform_services.kwai.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post, mock_get)
            with pytest.raises(OAuthError, match="user info fetch failed"):
                await kwai_service.handle_callback("c", "s")

    @pytest.mark.asyncio
    async def test_user_info_api_error(self, kwai_service):
        async def mock_post(url, **kw):
            return _mock_resp(200, {
                "result": 1,
                "access_token": "at", "refresh_token": "rt",
                "expires_in": 86400, "open_id": "uid",
            })

        async def mock_get(url, **kw):
            return _mock_resp(200, {
                "result": 0,
                "error_msg": "access denied",
            })

        with patch("platform_services.kwai.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post, mock_get)
            with pytest.raises(OAuthError, match="user info error"):
                await kwai_service.handle_callback("c", "s")


class TestKwaiRefreshToken:
    @pytest.mark.asyncio
    async def test_successful_refresh(self, kwai_service, valid_credential):
        async def mock_post(url, **kw):
            return _mock_resp(200, {
                "result": 1,
                "access_token": "kwai_at_refreshed",
                "refresh_token": "kwai_rt_refreshed",
                "expires_in": 86400,
            })

        with patch("platform_services.kwai.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            new_cred = await kwai_service.refresh_token(valid_credential)

        assert new_cred.access_token == "kwai_at_refreshed"
        assert new_cred.refresh_token == "kwai_rt_refreshed"
        assert new_cred.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_refresh_http_failure(self, kwai_service, valid_credential):
        async def mock_post(url, **kw):
            return _mock_resp(500, text="Error")

        with patch("platform_services.kwai.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            with pytest.raises(OAuthError, match="refresh failed"):
                await kwai_service.refresh_token(valid_credential)

    @pytest.mark.asyncio
    async def test_refresh_api_error(self, kwai_service, valid_credential):
        async def mock_post(url, **kw):
            return _mock_resp(200, {
                "result": 0,
                "error_msg": "refresh token expired",
            })

        with patch("platform_services.kwai.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            with pytest.raises(OAuthError, match="refresh error"):
                await kwai_service.refresh_token(valid_credential)


class TestKwaiCheckTokenStatus:
    @pytest.mark.asyncio
    async def test_valid_token(self, kwai_service, valid_credential):
        assert await kwai_service.check_token_status(valid_credential) is True

    @pytest.mark.asyncio
    async def test_expiring_token(self, kwai_service, expiring_credential):
        assert await kwai_service.check_token_status(expiring_credential) is False

    @pytest.mark.asyncio
    async def test_expired_token(self, kwai_service, expired_credential):
        assert await kwai_service.check_token_status(expired_credential) is False


# ############################################################################
#
#  4. XIAOHONGSHU (小红书)
#
# ############################################################################

@pytest.fixture
def xhs_service():
    return XiaohongshuService(
        client_id="xhs_test_id",
        client_secret="xhs_test_secret",
        redirect_uri="http://localhost:9000/api/oauth/callback/xiaohongshu",
    )


class TestXiaohongshuAttributes:
    def test_platform(self, xhs_service):
        assert xhs_service.platform == PlatformType.XIAOHONGSHU

    def test_auth_method(self, xhs_service):
        assert xhs_service.auth_method == AuthMethod.OAUTH2

    def test_client_config(self, xhs_service):
        assert xhs_service.client_id == "xhs_test_id"
        assert xhs_service.client_secret == "xhs_test_secret"


class TestXiaohongshuGetAuthUrl:
    @pytest.mark.asyncio
    async def test_url_contains_endpoint(self, xhs_service):
        url = await xhs_service.get_auth_url(state="st1")
        assert url.startswith(XHS_AUTH_URI)

    @pytest.mark.asyncio
    async def test_url_contains_app_id(self, xhs_service):
        """Xiaohongshu uses app_id in the URL."""
        url = await xhs_service.get_auth_url(state="st1")
        assert "app_id=xhs_test_id" in url

    @pytest.mark.asyncio
    async def test_url_contains_state(self, xhs_service):
        url = await xhs_service.get_auth_url(state="xhs_state_789")
        assert "xhs_state_789" in url

    @pytest.mark.asyncio
    async def test_url_contains_scopes(self, xhs_service):
        url = await xhs_service.get_auth_url(state="s")
        assert "user_info" in url
        assert "content_publish" in url

    @pytest.mark.asyncio
    async def test_url_contains_redirect_uri(self, xhs_service):
        url = await xhs_service.get_auth_url(state="s")
        assert "redirect_uri=" in url


class TestXiaohongshuHandleCallback:
    @pytest.mark.asyncio
    async def test_successful_callback(self, xhs_service):
        """Xiaohongshu uses code/msg pattern + json body for token exchange."""
        token_json = {
            "code": 0,
            "msg": "success",
            "data": {
                "access_token": "xhs_at_new",
                "refresh_token": "xhs_rt_new",
                "expires_in": 86400,
            },
        }
        user_json = {
            "code": 0,
            "msg": "success",
            "data": {
                "user_id": "xhs_uid_001",
                "nickname": "小红书博主",
                "avatar": "https://sns-avatar.xhscdn.com/avatar.jpg",
            },
        }

        async def mock_post(url, **kw):
            return _mock_resp(200, token_json)

        async def mock_get(url, **kw):
            return _mock_resp(200, user_json)

        with patch("platform_services.xiaohongshu.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post, mock_get)
            account, cred = await xhs_service.handle_callback("code1", "state1")

        assert account.platform == PlatformType.XIAOHONGSHU
        assert account.platform_uid == "xhs_uid_001"
        assert account.nickname == "小红书博主"
        assert account.avatar_url == "https://sns-avatar.xhscdn.com/avatar.jpg"
        assert cred.access_token == "xhs_at_new"
        assert cred.refresh_token == "xhs_rt_new"

    @pytest.mark.asyncio
    async def test_token_exchange_http_failure(self, xhs_service):
        async def mock_post(url, **kw):
            return _mock_resp(400, text="Bad Request")

        with patch("platform_services.xiaohongshu.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            with pytest.raises(OAuthError, match="token exchange failed"):
                await xhs_service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_token_exchange_api_error(self, xhs_service):
        """Xiaohongshu uses code != 0 and msg for errors."""
        async def mock_post(url, **kw):
            return _mock_resp(200, {
                "code": 40001,
                "msg": "invalid authorization code",
            })

        with patch("platform_services.xiaohongshu.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            with pytest.raises(OAuthError, match="token exchange error"):
                await xhs_service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_user_info_http_failure(self, xhs_service):
        async def mock_post(url, **kw):
            return _mock_resp(200, {
                "code": 0, "data": {
                    "access_token": "at", "refresh_token": "rt", "expires_in": 86400,
                },
            })

        async def mock_get(url, **kw):
            return _mock_resp(500, text="Error")

        with patch("platform_services.xiaohongshu.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post, mock_get)
            with pytest.raises(OAuthError, match="user info fetch failed"):
                await xhs_service.handle_callback("c", "s")

    @pytest.mark.asyncio
    async def test_user_info_api_error(self, xhs_service):
        async def mock_post(url, **kw):
            return _mock_resp(200, {
                "code": 0, "data": {
                    "access_token": "at", "refresh_token": "rt", "expires_in": 86400,
                },
            })

        async def mock_get(url, **kw):
            return _mock_resp(200, {"code": 40002, "msg": "unauthorized"})

        with patch("platform_services.xiaohongshu.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post, mock_get)
            with pytest.raises(OAuthError, match="user info error"):
                await xhs_service.handle_callback("c", "s")


class TestXiaohongshuRefreshToken:
    @pytest.mark.asyncio
    async def test_successful_refresh(self, xhs_service, valid_credential):
        """Xiaohongshu refresh uses json body (same as token exchange)."""
        async def mock_post(url, **kw):
            return _mock_resp(200, {
                "code": 0,
                "data": {
                    "access_token": "xhs_at_refreshed",
                    "refresh_token": "xhs_rt_refreshed",
                    "expires_in": 86400,
                },
            })

        with patch("platform_services.xiaohongshu.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            new_cred = await xhs_service.refresh_token(valid_credential)

        assert new_cred.access_token == "xhs_at_refreshed"
        assert new_cred.refresh_token == "xhs_rt_refreshed"
        assert new_cred.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_refresh_http_failure(self, xhs_service, valid_credential):
        async def mock_post(url, **kw):
            return _mock_resp(500, text="Error")

        with patch("platform_services.xiaohongshu.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            with pytest.raises(OAuthError, match="refresh failed"):
                await xhs_service.refresh_token(valid_credential)

    @pytest.mark.asyncio
    async def test_refresh_api_error(self, xhs_service, valid_credential):
        async def mock_post(url, **kw):
            return _mock_resp(200, {"code": 40003, "msg": "refresh expired"})

        with patch("platform_services.xiaohongshu.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_post)
            with pytest.raises(OAuthError, match="refresh error"):
                await xhs_service.refresh_token(valid_credential)


class TestXiaohongshuCheckTokenStatus:
    @pytest.mark.asyncio
    async def test_valid_token(self, xhs_service, valid_credential):
        assert await xhs_service.check_token_status(valid_credential) is True

    @pytest.mark.asyncio
    async def test_expiring_token(self, xhs_service, expiring_credential):
        assert await xhs_service.check_token_status(expiring_credential) is False

    @pytest.mark.asyncio
    async def test_expired_token(self, xhs_service, expired_credential):
        assert await xhs_service.check_token_status(expired_credential) is False


# ############################################################################
#
#  5. WEIXIN CHANNELS / SPH (微信视频号)
#
# ############################################################################

@pytest.fixture
def weixin_sph_service():
    return WeixinChannelsService(
        app_id="wx_sph_test_appid",
        app_secret="wx_sph_test_secret",
        redirect_uri="http://localhost:9000/api/oauth/callback/weixin_sph",
    )


class TestWeixinSphAttributes:
    def test_platform(self, weixin_sph_service):
        assert weixin_sph_service.platform == PlatformType.WEIXIN_SPH

    def test_auth_method(self, weixin_sph_service):
        assert weixin_sph_service.auth_method == AuthMethod.OAUTH2

    def test_client_config(self, weixin_sph_service):
        assert weixin_sph_service.app_id == "wx_sph_test_appid"
        assert weixin_sph_service.app_secret == "wx_sph_test_secret"


class TestWeixinSphGetAuthUrl:
    @pytest.mark.asyncio
    async def test_url_contains_endpoint(self, weixin_sph_service):
        url = await weixin_sph_service.get_auth_url(state="st1")
        assert WEIXIN_SPH_AUTH_URI in url

    @pytest.mark.asyncio
    async def test_url_contains_appid(self, weixin_sph_service):
        """WeChat uses appid instead of client_id."""
        url = await weixin_sph_service.get_auth_url(state="st1")
        assert "appid=wx_sph_test_appid" in url

    @pytest.mark.asyncio
    async def test_url_contains_state(self, weixin_sph_service):
        url = await weixin_sph_service.get_auth_url(state="wx_state_111")
        assert "wx_state_111" in url

    @pytest.mark.asyncio
    async def test_url_contains_scope(self, weixin_sph_service):
        url = await weixin_sph_service.get_auth_url(state="s")
        assert "snsapi_userinfo" in url

    @pytest.mark.asyncio
    async def test_url_ends_with_wechat_redirect(self, weixin_sph_service):
        """WeChat URLs must end with #wechat_redirect."""
        url = await weixin_sph_service.get_auth_url(state="s")
        assert url.endswith("#wechat_redirect")

    @pytest.mark.asyncio
    async def test_url_contains_redirect_uri(self, weixin_sph_service):
        url = await weixin_sph_service.get_auth_url(state="s")
        assert "redirect_uri=" in url


class TestWeixinSphHandleCallback:
    @pytest.mark.asyncio
    async def test_successful_callback(self, weixin_sph_service):
        """WeChat token exchange uses GET with appid/secret params; no errcode = success."""
        token_json = {
            "access_token": "wx_sph_at_new",
            "refresh_token": "wx_sph_rt_new",
            "expires_in": 7200,
            "openid": "wx_sph_openid_001",
            "scope": "snsapi_userinfo",
        }
        user_json = {
            "openid": "wx_sph_openid_001",
            "nickname": "视频号用户",
            "headimgurl": "https://thirdwx.qlogo.cn/avatar.jpg",
        }

        async def mock_get(url, **kw):
            if "access_token" in str(kw.get("params", {})) and "openid" in str(kw.get("params", {})):
                return _mock_resp(200, user_json)
            return _mock_resp(200, token_json)

        with patch("platform_services.weixin_channels.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_get=mock_get)
            account, cred = await weixin_sph_service.handle_callback("code1", "state1")

        assert account.platform == PlatformType.WEIXIN_SPH
        assert account.platform_uid == "wx_sph_openid_001"
        assert account.nickname == "视频号用户"
        assert account.avatar_url == "https://thirdwx.qlogo.cn/avatar.jpg"
        assert cred.access_token == "wx_sph_at_new"
        assert cred.refresh_token == "wx_sph_rt_new"
        assert cred.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_token_exchange_http_failure(self, weixin_sph_service):
        async def mock_get(url, **kw):
            return _mock_resp(500, text="Error")

        with patch("platform_services.weixin_channels.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_get=mock_get)
            with pytest.raises(OAuthError, match="token exchange failed"):
                await weixin_sph_service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_token_exchange_errcode(self, weixin_sph_service):
        """WeChat uses errcode/errmsg pattern for errors."""
        async def mock_get(url, **kw):
            return _mock_resp(200, {
                "errcode": 40029,
                "errmsg": "invalid code",
            })

        with patch("platform_services.weixin_channels.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_get=mock_get)
            with pytest.raises(OAuthError, match="token exchange error"):
                await weixin_sph_service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_user_info_http_failure(self, weixin_sph_service):
        call_count = {"n": 0}

        async def mock_get(url, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _mock_resp(200, {
                    "access_token": "at", "refresh_token": "rt",
                    "expires_in": 7200, "openid": "oid",
                })
            return _mock_resp(500, text="Error")

        with patch("platform_services.weixin_channels.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_get=mock_get)
            with pytest.raises(OAuthError, match="user info fetch failed"):
                await weixin_sph_service.handle_callback("c", "s")

    @pytest.mark.asyncio
    async def test_user_info_errcode(self, weixin_sph_service):
        call_count = {"n": 0}

        async def mock_get(url, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _mock_resp(200, {
                    "access_token": "at", "refresh_token": "rt",
                    "expires_in": 7200, "openid": "oid",
                })
            return _mock_resp(200, {"errcode": 40003, "errmsg": "invalid openid"})

        with patch("platform_services.weixin_channels.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_get=mock_get)
            with pytest.raises(OAuthError, match="user info error"):
                await weixin_sph_service.handle_callback("c", "s")


class TestWeixinSphRefreshToken:
    @pytest.mark.asyncio
    async def test_successful_refresh(self, weixin_sph_service, valid_credential):
        """WeChat refresh uses GET with appid param."""
        async def mock_get(url, **kw):
            return _mock_resp(200, {
                "access_token": "wx_sph_at_refreshed",
                "refresh_token": "wx_sph_rt_refreshed",
                "expires_in": 7200,
                "openid": "oid",
            })

        with patch("platform_services.weixin_channels.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_get=mock_get)
            new_cred = await weixin_sph_service.refresh_token(valid_credential)

        assert new_cred.access_token == "wx_sph_at_refreshed"
        assert new_cred.refresh_token == "wx_sph_rt_refreshed"
        assert new_cred.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_refresh_http_failure(self, weixin_sph_service, valid_credential):
        async def mock_get(url, **kw):
            return _mock_resp(500, text="Error")

        with patch("platform_services.weixin_channels.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_get=mock_get)
            with pytest.raises(OAuthError, match="refresh failed"):
                await weixin_sph_service.refresh_token(valid_credential)

    @pytest.mark.asyncio
    async def test_refresh_errcode(self, weixin_sph_service, valid_credential):
        async def mock_get(url, **kw):
            return _mock_resp(200, {"errcode": 40030, "errmsg": "invalid refresh_token"})

        with patch("platform_services.weixin_channels.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_get=mock_get)
            with pytest.raises(OAuthError, match="refresh error"):
                await weixin_sph_service.refresh_token(valid_credential)


class TestWeixinSphCheckTokenStatus:
    @pytest.mark.asyncio
    async def test_valid_token(self, weixin_sph_service, valid_credential):
        assert await weixin_sph_service.check_token_status(valid_credential) is True

    @pytest.mark.asyncio
    async def test_expiring_token(self, weixin_sph_service, expiring_credential):
        assert await weixin_sph_service.check_token_status(expiring_credential) is False

    @pytest.mark.asyncio
    async def test_expired_token(self, weixin_sph_service, expired_credential):
        assert await weixin_sph_service.check_token_status(expired_credential) is False


# ############################################################################
#
#  6. WEIXIN GZH (微信公众号)
#
# ############################################################################

@pytest.fixture
def weixin_gzh_service():
    return WeixinGzhService(
        app_id="wx_gzh_test_appid",
        app_secret="wx_gzh_test_secret",
        redirect_uri="http://localhost:9000/api/oauth/callback/weixin_gzh",
    )


class TestWeixinGzhAttributes:
    def test_platform(self, weixin_gzh_service):
        assert weixin_gzh_service.platform == PlatformType.WEIXIN_GZH

    def test_auth_method(self, weixin_gzh_service):
        assert weixin_gzh_service.auth_method == AuthMethod.OAUTH2

    def test_client_config(self, weixin_gzh_service):
        assert weixin_gzh_service.app_id == "wx_gzh_test_appid"
        assert weixin_gzh_service.app_secret == "wx_gzh_test_secret"


class TestWeixinGzhGetAuthUrl:
    @pytest.mark.asyncio
    async def test_url_contains_endpoint(self, weixin_gzh_service):
        url = await weixin_gzh_service.get_auth_url(state="st1")
        assert WEIXIN_GZH_AUTH_URI in url

    @pytest.mark.asyncio
    async def test_url_contains_appid(self, weixin_gzh_service):
        url = await weixin_gzh_service.get_auth_url(state="st1")
        assert "appid=wx_gzh_test_appid" in url

    @pytest.mark.asyncio
    async def test_url_contains_state(self, weixin_gzh_service):
        url = await weixin_gzh_service.get_auth_url(state="gzh_state_222")
        assert "gzh_state_222" in url

    @pytest.mark.asyncio
    async def test_url_contains_scope(self, weixin_gzh_service):
        url = await weixin_gzh_service.get_auth_url(state="s")
        assert "snsapi_userinfo" in url

    @pytest.mark.asyncio
    async def test_url_ends_with_wechat_redirect(self, weixin_gzh_service):
        url = await weixin_gzh_service.get_auth_url(state="s")
        assert url.endswith("#wechat_redirect")

    @pytest.mark.asyncio
    async def test_url_contains_redirect_uri(self, weixin_gzh_service):
        url = await weixin_gzh_service.get_auth_url(state="s")
        assert "redirect_uri=" in url


class TestWeixinGzhHandleCallback:
    @pytest.mark.asyncio
    async def test_successful_callback(self, weixin_gzh_service):
        """WeChat GZH also uses GET for token exchange with appid/secret."""
        token_json = {
            "access_token": "wx_gzh_at_new",
            "refresh_token": "wx_gzh_rt_new",
            "expires_in": 7200,
            "openid": "wx_gzh_openid_002",
            "scope": "snsapi_userinfo",
        }
        user_json = {
            "openid": "wx_gzh_openid_002",
            "nickname": "公众号粉丝",
            "headimgurl": "https://thirdwx.qlogo.cn/gzh_avatar.jpg",
        }

        async def mock_get(url, **kw):
            if "access_token" in str(kw.get("params", {})) and "openid" in str(kw.get("params", {})):
                return _mock_resp(200, user_json)
            return _mock_resp(200, token_json)

        with patch("platform_services.weixin_gzh.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_get=mock_get)
            account, cred = await weixin_gzh_service.handle_callback("code1", "state1")

        assert account.platform == PlatformType.WEIXIN_GZH
        assert account.platform_uid == "wx_gzh_openid_002"
        assert account.nickname == "公众号粉丝"
        assert account.avatar_url == "https://thirdwx.qlogo.cn/gzh_avatar.jpg"
        assert cred.access_token == "wx_gzh_at_new"
        assert cred.refresh_token == "wx_gzh_rt_new"
        assert cred.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_token_exchange_http_failure(self, weixin_gzh_service):
        async def mock_get(url, **kw):
            return _mock_resp(500, text="Error")

        with patch("platform_services.weixin_gzh.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_get=mock_get)
            with pytest.raises(OAuthError, match="token exchange failed"):
                await weixin_gzh_service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_token_exchange_errcode(self, weixin_gzh_service):
        async def mock_get(url, **kw):
            return _mock_resp(200, {
                "errcode": 40029,
                "errmsg": "invalid code",
            })

        with patch("platform_services.weixin_gzh.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_get=mock_get)
            with pytest.raises(OAuthError, match="token exchange error"):
                await weixin_gzh_service.handle_callback("bad", "s")

    @pytest.mark.asyncio
    async def test_user_info_http_failure(self, weixin_gzh_service):
        call_count = {"n": 0}

        async def mock_get(url, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _mock_resp(200, {
                    "access_token": "at", "refresh_token": "rt",
                    "expires_in": 7200, "openid": "oid",
                })
            return _mock_resp(500, text="Error")

        with patch("platform_services.weixin_gzh.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_get=mock_get)
            with pytest.raises(OAuthError, match="user info fetch failed"):
                await weixin_gzh_service.handle_callback("c", "s")

    @pytest.mark.asyncio
    async def test_user_info_errcode(self, weixin_gzh_service):
        call_count = {"n": 0}

        async def mock_get(url, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _mock_resp(200, {
                    "access_token": "at", "refresh_token": "rt",
                    "expires_in": 7200, "openid": "oid",
                })
            return _mock_resp(200, {"errcode": 40003, "errmsg": "invalid openid"})

        with patch("platform_services.weixin_gzh.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_get=mock_get)
            with pytest.raises(OAuthError, match="user info error"):
                await weixin_gzh_service.handle_callback("c", "s")


class TestWeixinGzhRefreshToken:
    @pytest.mark.asyncio
    async def test_successful_refresh(self, weixin_gzh_service, valid_credential):
        async def mock_get(url, **kw):
            return _mock_resp(200, {
                "access_token": "wx_gzh_at_refreshed",
                "refresh_token": "wx_gzh_rt_refreshed",
                "expires_in": 7200,
                "openid": "oid",
            })

        with patch("platform_services.weixin_gzh.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_get=mock_get)
            new_cred = await weixin_gzh_service.refresh_token(valid_credential)

        assert new_cred.access_token == "wx_gzh_at_refreshed"
        assert new_cred.refresh_token == "wx_gzh_rt_refreshed"
        assert new_cred.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_refresh_http_failure(self, weixin_gzh_service, valid_credential):
        async def mock_get(url, **kw):
            return _mock_resp(500, text="Error")

        with patch("platform_services.weixin_gzh.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_get=mock_get)
            with pytest.raises(OAuthError, match="refresh failed"):
                await weixin_gzh_service.refresh_token(valid_credential)

    @pytest.mark.asyncio
    async def test_refresh_errcode(self, weixin_gzh_service, valid_credential):
        async def mock_get(url, **kw):
            return _mock_resp(200, {"errcode": 40030, "errmsg": "invalid refresh_token"})

        with patch("platform_services.weixin_gzh.httpx.AsyncClient") as MC:
            MC.return_value = _build_mock_client(mock_get=mock_get)
            with pytest.raises(OAuthError, match="refresh error"):
                await weixin_gzh_service.refresh_token(valid_credential)


class TestWeixinGzhCheckTokenStatus:
    @pytest.mark.asyncio
    async def test_valid_token(self, weixin_gzh_service, valid_credential):
        assert await weixin_gzh_service.check_token_status(valid_credential) is True

    @pytest.mark.asyncio
    async def test_expiring_token(self, weixin_gzh_service, expiring_credential):
        assert await weixin_gzh_service.check_token_status(expiring_credential) is False

    @pytest.mark.asyncio
    async def test_expired_token(self, weixin_gzh_service, expired_credential):
        assert await weixin_gzh_service.check_token_status(expired_credential) is False
