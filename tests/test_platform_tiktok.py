"""
Sprint 4: TikTokService 单元测试。

覆盖 OAuth 流程（get_auth_url, handle_callback, refresh_token, check_token_status）
以及视频发布（publish_video）。所有 HTTP 调用均使用 mock。
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
    PlatformType,
)
from platform_services.exceptions import OAuthError, PublishError  # noqa: E402
from platform_services.tiktok import (  # noqa: E402
    AUTH_URI,
    PUBLISH_INIT_URI,
    SCOPES,
    TOKEN_URI,
    USER_INFO_URI,
    TikTokService,
)


@pytest.fixture
def tiktok_service():
    return TikTokService(
        client_id="test_client_key",
        client_secret="test_client_secret",
        redirect_uri="http://localhost:9000/api/oauth/callback/tiktok",
    )


@pytest.fixture
def valid_credential():
    return OAuthCredential(
        access_token="act.test_access_token",
        refresh_token="rft.test_refresh_token",
        expires_at=int(time.time()) + 86400,
    )


@pytest.fixture
def expiring_credential():
    return OAuthCredential(
        access_token="act.expiring_token",
        refresh_token="rft.test_refresh_token",
        expires_at=int(time.time()) + 300,  # 5 minutes, within 600s buffer
    )


# ---------------------------------------------------------------------------
# Class attributes
# ---------------------------------------------------------------------------

class TestTikTokServiceAttributes:
    def test_platform(self, tiktok_service):
        assert tiktok_service.platform == PlatformType.TIKTOK

    def test_auth_method(self, tiktok_service):
        assert tiktok_service.auth_method == AuthMethod.OAUTH2

    def test_client_config(self, tiktok_service):
        assert tiktok_service.client_id == "test_client_key"
        assert tiktok_service.client_secret == "test_client_secret"
        assert tiktok_service.redirect_uri == "http://localhost:9000/api/oauth/callback/tiktok"


# ---------------------------------------------------------------------------
# get_auth_url
# ---------------------------------------------------------------------------

class TestGetAuthUrl:
    @pytest.mark.asyncio
    async def test_generates_correct_url(self, tiktok_service):
        url = await tiktok_service.get_auth_url(state="test_state_123")
        assert url.startswith(AUTH_URI)
        assert "client_key=test_client_key" in url
        assert "state=test_state_123" in url
        assert "response_type=code" in url

    @pytest.mark.asyncio
    async def test_includes_scopes(self, tiktok_service):
        url = await tiktok_service.get_auth_url(state="s")
        assert "user.info.basic" in url
        assert "video.publish" in url
        assert "video.upload" in url

    @pytest.mark.asyncio
    async def test_includes_redirect_uri(self, tiktok_service):
        url = await tiktok_service.get_auth_url(state="s")
        assert "redirect_uri=" in url
        assert "localhost" in url


# ---------------------------------------------------------------------------
# handle_callback
# ---------------------------------------------------------------------------

class TestHandleCallback:
    @pytest.mark.asyncio
    async def test_successful_callback(self, tiktok_service):
        """测试成功的 OAuth 回调：token 交换 + 用户信息获取。"""
        token_response = {
            "access_token": "act.new_token",
            "refresh_token": "rft.new_refresh",
            "expires_in": 86400,
            "open_id": "tiktok_user_123",
            "token_type": "Bearer",
        }
        user_response = {
            "data": {
                "user": {
                    "open_id": "tiktok_user_123",
                    "display_name": "TikTok Creator",
                    "avatar_url": "https://p16.tiktokcdn.com/avatar.jpg",
                }
            },
            "error": {"code": "ok", "message": ""},
        }

        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = token_response

        mock_user_resp = MagicMock()
        mock_user_resp.status_code = 200
        mock_user_resp.json.return_value = user_response

        async def mock_post(url, **kwargs):
            return mock_token_resp

        async def mock_get(url, **kwargs):
            return mock_user_resp

        with patch("platform_services.tiktok.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            account, credential = await tiktok_service.handle_callback(
                code="test_code", state="test_state"
            )

        assert account.platform == PlatformType.TIKTOK
        assert account.platform_uid == "tiktok_user_123"
        assert account.nickname == "TikTok Creator"
        assert account.avatar_url == "https://p16.tiktokcdn.com/avatar.jpg"
        assert credential.access_token == "act.new_token"
        assert credential.refresh_token == "rft.new_refresh"

    @pytest.mark.asyncio
    async def test_token_exchange_failure(self, tiktok_service):
        """token 交换 HTTP 失败时应抛出 OAuthError。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "invalid_grant"

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.tiktok.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="token exchange failed"):
                await tiktok_service.handle_callback(code="bad", state="s")

    @pytest.mark.asyncio
    async def test_token_exchange_api_error(self, tiktok_service):
        """token 交换返回 API 错误时应抛出 OAuthError。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "error": "invalid_client",
            "error_description": "Client authentication failed",
        }

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.tiktok.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="token exchange error"):
                await tiktok_service.handle_callback(code="bad", state="s")

    @pytest.mark.asyncio
    async def test_user_info_http_failure(self, tiktok_service):
        """用户信息 HTTP 请求失败时应抛出 OAuthError。"""
        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 86400,
            "open_id": "uid",
        }

        mock_user_resp = MagicMock()
        mock_user_resp.status_code = 401
        mock_user_resp.text = "Unauthorized"

        async def mock_post(url, **kwargs):
            return mock_token_resp

        async def mock_get(url, **kwargs):
            return mock_user_resp

        with patch("platform_services.tiktok.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="user info fetch failed"):
                await tiktok_service.handle_callback(code="c", state="s")

    @pytest.mark.asyncio
    async def test_user_info_api_error(self, tiktok_service):
        """用户信息 API 返回错误时应抛出 OAuthError。"""
        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 86400,
            "open_id": "uid",
        }

        mock_user_resp = MagicMock()
        mock_user_resp.status_code = 200
        mock_user_resp.json.return_value = {
            "data": {"user": {}},
            "error": {"code": "access_token_invalid", "message": "Token expired"},
        }

        async def mock_post(url, **kwargs):
            return mock_token_resp

        async def mock_get(url, **kwargs):
            return mock_user_resp

        with patch("platform_services.tiktok.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="user info error"):
                await tiktok_service.handle_callback(code="c", state="s")


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------

class TestRefreshToken:
    @pytest.mark.asyncio
    async def test_successful_refresh(self, tiktok_service, valid_credential):
        """刷新成功应返回新凭证。"""
        refresh_response = {
            "access_token": "act.refreshed_token",
            "refresh_token": "rft.new_refresh",
            "expires_in": 86400,
            "open_id": "tiktok_user_123",
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = refresh_response

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.tiktok.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            new_credential = await tiktok_service.refresh_token(valid_credential)

        assert new_credential.access_token == "act.refreshed_token"
        assert new_credential.refresh_token == "rft.new_refresh"
        assert new_credential.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_refresh_http_failure(self, tiktok_service, valid_credential):
        """刷新 HTTP 失败时应抛出 OAuthError。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "invalid_grant"

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.tiktok.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="refresh failed"):
                await tiktok_service.refresh_token(valid_credential)

    @pytest.mark.asyncio
    async def test_refresh_api_error(self, tiktok_service, valid_credential):
        """刷新返回 API 错误时应抛出 OAuthError。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "error": "invalid_refresh_token",
            "error_description": "Refresh token expired",
        }

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.tiktok.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="refresh error"):
                await tiktok_service.refresh_token(valid_credential)


# ---------------------------------------------------------------------------
# check_token_status
# ---------------------------------------------------------------------------

class TestCheckTokenStatus:
    @pytest.mark.asyncio
    async def test_valid_token(self, tiktok_service, valid_credential):
        """距离过期 > 600s 的 token 应返回 True。"""
        result = await tiktok_service.check_token_status(valid_credential)
        assert result is True

    @pytest.mark.asyncio
    async def test_expiring_token(self, tiktok_service, expiring_credential):
        """距离过期 < 600s 的 token 应返回 False。"""
        result = await tiktok_service.check_token_status(expiring_credential)
        assert result is False

    @pytest.mark.asyncio
    async def test_expired_token(self, tiktok_service):
        """已过期 token 应返回 False。"""
        cred = OAuthCredential(
            access_token="expired",
            refresh_token="rt",
            expires_at=int(time.time()) - 100,
        )
        result = await tiktok_service.check_token_status(cred)
        assert result is False


# ---------------------------------------------------------------------------
# publish_video
# ---------------------------------------------------------------------------

class TestPublishVideo:
    @pytest.mark.asyncio
    async def test_successful_upload(self, tiktok_service, valid_credential, tmp_path):
        """测试成功的视频上传（init + chunk upload）。"""
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 1024)

        init_response = {
            "data": {
                "publish_id": "pub_123456",
                "upload_url": "https://upload.tiktokapis.com/video/?upload_id=123",
            },
            "error": {"code": "ok", "message": ""},
        }

        mock_init_resp = MagicMock()
        mock_init_resp.status_code = 200
        mock_init_resp.json.return_value = init_response

        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 201

        call_count = {"post": 0}

        async def mock_post(url, **kwargs):
            call_count["post"] += 1
            return mock_init_resp

        async def mock_put(url, **kwargs):
            return mock_upload_resp

        with patch("platform_services.tiktok.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await tiktok_service.publish_video(
                credential=valid_credential,
                video_path=str(video_file),
                title="Test TikTok Video",
                description="Test description",
                tags=["test", "tiktok"],
            )

        assert result.success is True
        assert result.post_id == "pub_123456"
        assert result.status == "publishing"

    @pytest.mark.asyncio
    async def test_init_http_failure(self, tiktok_service, valid_credential, tmp_path):
        """upload init HTTP 失败时应抛出 PublishError。"""
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.tiktok.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="upload init failed"):
                await tiktok_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Fail Video",
                )

    @pytest.mark.asyncio
    async def test_init_api_error(self, tiktok_service, valid_credential, tmp_path):
        """upload init 返回 API 错误时应抛出 PublishError。"""
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {},
            "error": {"code": "spam_risk_too_many_posts", "message": "Too many posts"},
        }

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.tiktok.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="upload init error"):
                await tiktok_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Fail Video",
                )

    @pytest.mark.asyncio
    async def test_missing_upload_url(self, tiktok_service, valid_credential, tmp_path):
        """upload init 缺少 upload_url 时应抛出 PublishError。"""
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {"publish_id": "pub_123"},
            "error": {"code": "ok", "message": ""},
        }

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.tiktok.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="missing upload_url"):
                await tiktok_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Fail Video",
                )

    @pytest.mark.asyncio
    async def test_chunk_upload_failure(self, tiktok_service, valid_credential, tmp_path):
        """chunk 上传失败时应抛出 PublishError。"""
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 1024)

        init_response = {
            "data": {
                "publish_id": "pub_123456",
                "upload_url": "https://upload.tiktokapis.com/video/?upload_id=123",
            },
            "error": {"code": "ok", "message": ""},
        }

        mock_init_resp = MagicMock()
        mock_init_resp.status_code = 200
        mock_init_resp.json.return_value = init_response

        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 500

        async def mock_post(url, **kwargs):
            return mock_init_resp

        async def mock_put(url, **kwargs):
            return mock_upload_resp

        with patch("platform_services.tiktok.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="chunk upload failed"):
                await tiktok_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Fail Video",
                )
