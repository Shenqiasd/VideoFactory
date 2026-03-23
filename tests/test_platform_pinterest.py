"""
Sprint 4: PinterestService 单元测试。

覆盖 OAuth 流程（get_auth_url, handle_callback, refresh_token, check_token_status）
以及视频发布（media registration + upload + pin creation）。所有 HTTP 调用均使用 mock。
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
from platform_services.pinterest import (  # noqa: E402
    AUTH_URI,
    MEDIA_URI,
    PINS_URI,
    TOKEN_URI,
    PinterestService,
)


@pytest.fixture
def pinterest_service():
    return PinterestService(
        client_id="pin_test_client_id",
        client_secret="pin_test_client_secret",
        redirect_uri="http://localhost:9000/api/oauth/callback/pinterest",
    )


@pytest.fixture
def valid_credential():
    return OAuthCredential(
        access_token="pin_access_token_123",
        refresh_token="pin_refresh_token_456",
        expires_at=int(time.time()) + 3600,
    )


@pytest.fixture
def expiring_credential():
    return OAuthCredential(
        access_token="pin_expiring_token",
        refresh_token="pin_refresh_token",
        expires_at=int(time.time()) + 300,
    )


# ---------------------------------------------------------------------------
# Class attributes
# ---------------------------------------------------------------------------

class TestPinterestServiceAttributes:
    def test_platform(self, pinterest_service):
        assert pinterest_service.platform == PlatformType.PINTEREST

    def test_auth_method(self, pinterest_service):
        assert pinterest_service.auth_method == AuthMethod.OAUTH2

    def test_client_config(self, pinterest_service):
        assert pinterest_service.client_id == "pin_test_client_id"
        assert pinterest_service.client_secret == "pin_test_client_secret"


# ---------------------------------------------------------------------------
# get_auth_url
# ---------------------------------------------------------------------------

class TestGetAuthUrl:
    @pytest.mark.asyncio
    async def test_generates_correct_url(self, pinterest_service):
        url = await pinterest_service.get_auth_url(state="test_state_abc")
        assert url.startswith(AUTH_URI)
        assert "client_id=pin_test_client_id" in url
        assert "state=test_state_abc" in url
        assert "response_type=code" in url

    @pytest.mark.asyncio
    async def test_includes_scopes(self, pinterest_service):
        url = await pinterest_service.get_auth_url(state="s")
        for scope_part in ["boards%3Aread", "pins%3Aread", "pins%3Awrite"]:
            assert scope_part in url

    @pytest.mark.asyncio
    async def test_includes_redirect_uri(self, pinterest_service):
        url = await pinterest_service.get_auth_url(state="s")
        assert "redirect_uri=" in url
        assert "localhost" in url


# ---------------------------------------------------------------------------
# handle_callback
# ---------------------------------------------------------------------------

class TestHandleCallback:
    @pytest.mark.asyncio
    async def test_successful_callback(self, pinterest_service):
        """测试成功的 OAuth 回调：token 交换 + 用户信息获取。"""
        token_response = {
            "access_token": "pin_new_access_token",
            "refresh_token": "pin_new_refresh_token",
            "expires_in": 3600,
            "token_type": "bearer",
        }
        user_response = {
            "username": "pinuser",
            "business_name": "Pin User",
            "profile_image": "https://i.pinimg.com/test.jpg",
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

        with patch("platform_services.pinterest.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            account, credential = await pinterest_service.handle_callback(
                code="test_code", state="test_state"
            )

        assert account.platform == PlatformType.PINTEREST
        assert account.platform_uid == "pinuser"
        assert account.username == "pinuser"
        assert account.nickname == "Pin User"
        assert account.avatar_url == "https://i.pinimg.com/test.jpg"
        assert credential.access_token == "pin_new_access_token"
        assert credential.refresh_token == "pin_new_refresh_token"

    @pytest.mark.asyncio
    async def test_token_exchange_failure(self, pinterest_service):
        """token 交换失败时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "invalid_grant"

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.pinterest.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="token exchange failed"):
                await pinterest_service.handle_callback(code="bad", state="s")

    @pytest.mark.asyncio
    async def test_user_info_fetch_failure(self, pinterest_service):
        """用户信息获取失败时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 3600,
        }

        mock_user_resp = MagicMock()
        mock_user_resp.status_code = 401
        mock_user_resp.text = "Unauthorized"

        async def mock_post(url, **kwargs):
            return mock_token_resp

        async def mock_get(url, **kwargs):
            return mock_user_resp

        with patch("platform_services.pinterest.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="user info fetch failed"):
                await pinterest_service.handle_callback(code="c", state="s")


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------

class TestRefreshToken:
    @pytest.mark.asyncio
    async def test_returns_new_tokens(self, pinterest_service, valid_credential):
        """刷新后应返回新的 access_token。"""
        refresh_response = {
            "access_token": "pin_refreshed_access",
            "refresh_token": "pin_new_refresh_999",
            "expires_in": 3600,
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = refresh_response

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.pinterest.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            new_credential = await pinterest_service.refresh_token(valid_credential)

        assert new_credential.access_token == "pin_refreshed_access"
        assert new_credential.refresh_token == "pin_new_refresh_999"
        assert new_credential.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_refresh_failure(self, pinterest_service, valid_credential):
        """刷新失败时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "invalid_grant"

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.pinterest.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="refresh failed"):
                await pinterest_service.refresh_token(valid_credential)


# ---------------------------------------------------------------------------
# check_token_status
# ---------------------------------------------------------------------------

class TestCheckTokenStatus:
    @pytest.mark.asyncio
    async def test_valid_token(self, pinterest_service, valid_credential):
        result = await pinterest_service.check_token_status(valid_credential)
        assert result is True

    @pytest.mark.asyncio
    async def test_expiring_token(self, pinterest_service, expiring_credential):
        result = await pinterest_service.check_token_status(expiring_credential)
        assert result is False

    @pytest.mark.asyncio
    async def test_expired_token(self, pinterest_service):
        cred = OAuthCredential(
            access_token="expired",
            refresh_token="rt",
            expires_at=int(time.time()) - 100,
        )
        result = await pinterest_service.check_token_status(cred)
        assert result is False


# ---------------------------------------------------------------------------
# publish_video
# ---------------------------------------------------------------------------

class TestPublishVideo:
    @pytest.mark.asyncio
    async def test_successful_upload(self, pinterest_service, valid_credential, tmp_path):
        """测试完整流程: register media → upload → check status → create pin。"""
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 1024)

        register_response = {
            "media_id": "pin_media_123",
            "upload_url": "https://pinterest-media-upload.s3.amazonaws.com/upload/123",
        }
        status_response = {
            "status": "succeeded",
        }
        pin_response = {
            "id": "pin_456",
        }

        call_log = []

        async def mock_post(url, **kwargs):
            call_log.append(("POST", url))
            resp = MagicMock()
            if MEDIA_URI in url and PINS_URI not in url:
                resp.status_code = 201
                resp.json.return_value = register_response
            else:
                resp.status_code = 201
                resp.json.return_value = pin_response
            return resp

        async def mock_put(url, **kwargs):
            call_log.append(("PUT", url))
            resp = MagicMock()
            resp.status_code = 204
            return resp

        async def mock_get(url, **kwargs):
            call_log.append(("GET", url))
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = status_response
            return resp

        with patch("platform_services.pinterest.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await pinterest_service.publish_video(
                credential=valid_credential,
                video_path=str(video_file),
                title="Test Pin Video",
                description="Test description",
                board_id="board_789",
            )

        assert result.success is True
        assert result.post_id == "pin_456"
        assert "pinterest.com/pin/pin_456" in result.permalink

    @pytest.mark.asyncio
    async def test_media_registration_failure(self, pinterest_service, valid_credential, tmp_path):
        """Media 注册失败时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "fail_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 400
            resp.text = "Bad Request"
            return resp

        with patch("platform_services.pinterest.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="media registration failed"):
                await pinterest_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Fail",
                )

    @pytest.mark.asyncio
    async def test_video_upload_failure(self, pinterest_service, valid_credential, tmp_path):
        """视频上传失败时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "upload_fail.mp4"
        video_file.write_bytes(b"\x00" * 512)

        register_response = {
            "media_id": "pin_media_fail",
            "upload_url": "https://upload.example.com/fail",
        }

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 201
            resp.json.return_value = register_response
            return resp

        async def mock_put(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 500
            return resp

        with patch("platform_services.pinterest.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="video upload failed"):
                await pinterest_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Upload Fail",
                )

    @pytest.mark.asyncio
    async def test_media_processing_failed(self, pinterest_service, valid_credential, tmp_path):
        """Media 处理失败时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "proc_fail.mp4"
        video_file.write_bytes(b"\x00" * 512)

        register_response = {
            "media_id": "pin_media_proc",
            "upload_url": "https://upload.example.com/ok",
        }

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 201
            resp.json.return_value = register_response
            return resp

        async def mock_put(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 204
            return resp

        async def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"status": "failed"}
            return resp

        with patch("platform_services.pinterest.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="media processing failed"):
                await pinterest_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Processing Fail",
                )

    @pytest.mark.asyncio
    async def test_pin_creation_failure(self, pinterest_service, valid_credential, tmp_path):
        """Pin 创建失败时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "pin_fail.mp4"
        video_file.write_bytes(b"\x00" * 512)

        register_response = {
            "media_id": "pin_media_ok",
            "upload_url": "https://upload.example.com/ok",
        }

        post_count = {"n": 0}

        async def mock_post(url, **kwargs):
            post_count["n"] += 1
            resp = MagicMock()
            if post_count["n"] == 1:
                # media registration
                resp.status_code = 201
                resp.json.return_value = register_response
            else:
                # pin creation
                resp.status_code = 400
                resp.text = "Bad Request"
                resp.json.return_value = {}
            return resp

        async def mock_put(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 204
            return resp

        async def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"status": "succeeded"}
            return resp

        with patch("platform_services.pinterest.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="pin creation failed"):
                await pinterest_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Pin Fail",
                )

    @pytest.mark.asyncio
    async def test_missing_media_id_or_upload_url(self, pinterest_service, valid_credential, tmp_path):
        """Media registration returns no media_id → raise PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "no_media.mp4"
        video_file.write_bytes(b"\x00" * 512)

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 201
            resp.json.return_value = {"media_id": "", "upload_url": ""}
            return resp

        with patch("platform_services.pinterest.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="missing media_id or upload_url"):
                await pinterest_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="No Media",
                )
