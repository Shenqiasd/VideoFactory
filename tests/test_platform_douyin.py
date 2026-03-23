"""
Sprint 4: DouyinService 单元测试。

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
from platform_services.douyin import (  # noqa: E402
    AUTH_URI,
    CREATE_URI,
    REFRESH_URI,
    TOKEN_URI,
    UPLOAD_URI,
    USER_INFO_URI,
    DouyinService,
)


@pytest.fixture
def douyin_service():
    return DouyinService(
        client_key="test_client_key",
        client_secret="test_client_secret",
        redirect_uri="http://localhost:9000/api/oauth/callback/douyin",
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

class TestDouyinServiceAttributes:
    def test_platform(self, douyin_service):
        assert douyin_service.platform == PlatformType.DOUYIN

    def test_auth_method(self, douyin_service):
        assert douyin_service.auth_method == AuthMethod.OAUTH2

    def test_client_config(self, douyin_service):
        assert douyin_service.client_key == "test_client_key"
        assert douyin_service.client_secret == "test_client_secret"
        assert douyin_service.redirect_uri == "http://localhost:9000/api/oauth/callback/douyin"


# ---------------------------------------------------------------------------
# get_auth_url
# ---------------------------------------------------------------------------

class TestGetAuthUrl:
    @pytest.mark.asyncio
    async def test_generates_correct_url(self, douyin_service):
        url = await douyin_service.get_auth_url(state="test_state_123")
        assert url.startswith(AUTH_URI)
        assert "client_key=test_client_key" in url
        assert "state=test_state_123" in url
        assert "response_type=code" in url

    @pytest.mark.asyncio
    async def test_includes_scopes(self, douyin_service):
        url = await douyin_service.get_auth_url(state="s")
        assert "user_info" in url
        assert "video.create" in url
        assert "video.data" in url

    @pytest.mark.asyncio
    async def test_includes_redirect_uri(self, douyin_service):
        url = await douyin_service.get_auth_url(state="s")
        assert "redirect_uri=" in url
        assert "localhost" in url


# ---------------------------------------------------------------------------
# handle_callback
# ---------------------------------------------------------------------------

class TestHandleCallback:
    @pytest.mark.asyncio
    async def test_successful_callback(self, douyin_service):
        """测试成功的 OAuth 回调：token 交换 + 用户信息获取。"""
        token_response = {
            "data": {
                "access_token": "act.new_token",
                "refresh_token": "rft.new_refresh",
                "expires_in": 86400,
                "open_id": "douyin_user_123",
                "error_code": 0,
                "description": "",
            },
            "extra": {"logid": "log123"},
        }
        user_response = {
            "data": {
                "open_id": "douyin_user_123",
                "nickname": "抖音创作者",
                "avatar": "https://p3.douyinpic.com/avatar.jpg",
                "error_code": 0,
                "description": "",
            },
            "extra": {"logid": "log456"},
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

        with patch("platform_services.douyin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            account, credential = await douyin_service.handle_callback(
                code="test_code", state="test_state"
            )

        assert account.platform == PlatformType.DOUYIN
        assert account.platform_uid == "douyin_user_123"
        assert account.nickname == "抖音创作者"
        assert account.avatar_url == "https://p3.douyinpic.com/avatar.jpg"
        assert credential.access_token == "act.new_token"
        assert credential.refresh_token == "rft.new_refresh"

    @pytest.mark.asyncio
    async def test_token_exchange_http_failure(self, douyin_service):
        """token 交换 HTTP 失败时应抛出 OAuthError。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request"

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.douyin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="token exchange failed"):
                await douyin_service.handle_callback(code="bad", state="s")

    @pytest.mark.asyncio
    async def test_token_exchange_api_error(self, douyin_service):
        """token 交换返回 API 错误时应抛出 OAuthError。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "error_code": 10008,
                "description": "Invalid authorization code",
            },
            "extra": {"logid": "log789"},
        }

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.douyin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="token exchange error"):
                await douyin_service.handle_callback(code="bad", state="s")

    @pytest.mark.asyncio
    async def test_user_info_http_failure(self, douyin_service):
        """用户信息 HTTP 请求失败时应抛出 OAuthError。"""
        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {
            "data": {
                "access_token": "at",
                "refresh_token": "rt",
                "expires_in": 86400,
                "open_id": "uid",
                "error_code": 0,
            },
        }

        mock_user_resp = MagicMock()
        mock_user_resp.status_code = 500
        mock_user_resp.text = "Internal Server Error"

        async def mock_post(url, **kwargs):
            return mock_token_resp

        async def mock_get(url, **kwargs):
            return mock_user_resp

        with patch("platform_services.douyin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="user info fetch failed"):
                await douyin_service.handle_callback(code="c", state="s")

    @pytest.mark.asyncio
    async def test_user_info_api_error(self, douyin_service):
        """用户信息 API 返回错误时应抛出 OAuthError。"""
        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {
            "data": {
                "access_token": "at",
                "refresh_token": "rt",
                "expires_in": 86400,
                "open_id": "uid",
                "error_code": 0,
            },
        }

        mock_user_resp = MagicMock()
        mock_user_resp.status_code = 200
        mock_user_resp.json.return_value = {
            "data": {
                "error_code": 10002,
                "description": "Access token expired",
            },
        }

        async def mock_post(url, **kwargs):
            return mock_token_resp

        async def mock_get(url, **kwargs):
            return mock_user_resp

        with patch("platform_services.douyin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="user info error"):
                await douyin_service.handle_callback(code="c", state="s")


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------

class TestRefreshToken:
    @pytest.mark.asyncio
    async def test_successful_refresh(self, douyin_service, valid_credential):
        """刷新成功应返回新的 access_token 和 refresh_token。"""
        refresh_response = {
            "data": {
                "access_token": "act.refreshed_token",
                "refresh_token": "rft.renewed_refresh",
                "expires_in": 86400,
                "error_code": 0,
                "description": "",
            },
            "extra": {"logid": "log_refresh"},
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = refresh_response

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.douyin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            new_credential = await douyin_service.refresh_token(valid_credential)

        assert new_credential.access_token == "act.refreshed_token"
        assert new_credential.refresh_token == "rft.renewed_refresh"
        assert new_credential.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_refresh_http_failure(self, douyin_service, valid_credential):
        """刷新 HTTP 失败时应抛出 OAuthError。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.douyin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="refresh failed"):
                await douyin_service.refresh_token(valid_credential)

    @pytest.mark.asyncio
    async def test_refresh_api_error(self, douyin_service, valid_credential):
        """刷新返回 API 错误时应抛出 OAuthError。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "error_code": 10010,
                "description": "Refresh token expired",
            },
            "extra": {"logid": "log_err"},
        }

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.douyin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="refresh error"):
                await douyin_service.refresh_token(valid_credential)


# ---------------------------------------------------------------------------
# check_token_status
# ---------------------------------------------------------------------------

class TestCheckTokenStatus:
    @pytest.mark.asyncio
    async def test_valid_token(self, douyin_service, valid_credential):
        """距离过期 > 600s 的 token 应返回 True。"""
        result = await douyin_service.check_token_status(valid_credential)
        assert result is True

    @pytest.mark.asyncio
    async def test_expiring_token(self, douyin_service, expiring_credential):
        """距离过期 < 600s 的 token 应返回 False。"""
        result = await douyin_service.check_token_status(expiring_credential)
        assert result is False

    @pytest.mark.asyncio
    async def test_expired_token(self, douyin_service):
        """已过期 token 应返回 False。"""
        cred = OAuthCredential(
            access_token="expired",
            refresh_token="rt",
            expires_at=int(time.time()) - 100,
        )
        result = await douyin_service.check_token_status(cred)
        assert result is False


# ---------------------------------------------------------------------------
# publish_video
# ---------------------------------------------------------------------------

class TestPublishVideo:
    @pytest.mark.asyncio
    async def test_successful_publish(self, douyin_service, valid_credential, tmp_path):
        """测试成功的视频上传 + 创建投稿。"""
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 1024)

        upload_response = {
            "data": {
                "video": {"video_id": "vid_123456"},
                "error_code": 0,
                "description": "",
            },
            "extra": {"logid": "log_upload"},
        }
        create_response = {
            "data": {
                "item_id": "item_789",
                "error_code": 0,
                "description": "",
            },
            "extra": {"logid": "log_create"},
        }

        call_urls = []

        async def mock_post(url, **kwargs):
            call_urls.append(url)
            if "upload" in url:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = upload_response
                return mock_resp
            else:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = create_response
                return mock_resp

        with patch("platform_services.douyin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await douyin_service.publish_video(
                credential=valid_credential,
                video_path=str(video_file),
                title="抖音测试视频",
                description="测试描述",
                tags=["测试", "抖音"],
            )

        assert result.success is True
        assert result.post_id == "item_789"
        assert "douyin.com/video/item_789" in result.permalink
        assert result.status == "published"

    @pytest.mark.asyncio
    async def test_upload_http_failure(self, douyin_service, valid_credential, tmp_path):
        """视频上传 HTTP 失败时应抛出 PublishError。"""
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.douyin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="video upload failed"):
                await douyin_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Fail Video",
                )

    @pytest.mark.asyncio
    async def test_upload_api_error(self, douyin_service, valid_credential, tmp_path):
        """视频上传返回 API 错误时应抛出 PublishError。"""
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "error_code": 40051,
                "description": "Video format not supported",
            },
            "extra": {"logid": "log_err"},
        }

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.douyin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="video upload error"):
                await douyin_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Fail Video",
                )

    @pytest.mark.asyncio
    async def test_missing_video_id(self, douyin_service, valid_credential, tmp_path):
        """上传成功但缺少 video_id 时应抛出 PublishError。"""
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "video": {},
                "error_code": 0,
            },
            "extra": {},
        }

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.douyin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="missing video_id"):
                await douyin_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Fail Video",
                )

    @pytest.mark.asyncio
    async def test_create_post_failure(self, douyin_service, valid_credential, tmp_path):
        """创建投稿失败时应抛出 PublishError。"""
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        upload_response = {
            "data": {
                "video": {"video_id": "vid_123456"},
                "error_code": 0,
            },
            "extra": {},
        }
        create_response = {
            "data": {
                "error_code": 40003,
                "description": "Content policy violation",
            },
            "extra": {},
        }

        call_count = {"n": 0}

        async def mock_post(url, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = upload_response
                return mock_resp
            else:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = create_response
                return mock_resp

        with patch("platform_services.douyin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="create post error"):
                await douyin_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Policy Fail",
                )

    @pytest.mark.asyncio
    async def test_create_post_http_failure(self, douyin_service, valid_credential, tmp_path):
        """创建投稿 HTTP 失败时应抛出 PublishError。"""
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        upload_response = {
            "data": {
                "video": {"video_id": "vid_123456"},
                "error_code": 0,
            },
            "extra": {},
        }

        call_count = {"n": 0}

        async def mock_post(url, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = upload_response
                return mock_resp
            else:
                mock_resp = MagicMock()
                mock_resp.status_code = 503
                mock_resp.text = "Service Unavailable"
                return mock_resp

        with patch("platform_services.douyin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="create post failed"):
                await douyin_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Fail Video",
                )

    @pytest.mark.asyncio
    async def test_publish_with_tags(self, douyin_service, valid_credential, tmp_path):
        """测试发布时标签被正确添加到文本中。"""
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        upload_response = {
            "data": {
                "video": {"video_id": "vid_tag_test"},
                "error_code": 0,
            },
            "extra": {},
        }
        create_response = {
            "data": {
                "item_id": "item_tag_test",
                "error_code": 0,
            },
            "extra": {},
        }

        captured_body = {}

        async def mock_post(url, **kwargs):
            if "create" in url:
                captured_body.update(kwargs.get("json", {}))
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = create_response
                return mock_resp
            else:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = upload_response
                return mock_resp

        with patch("platform_services.douyin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await douyin_service.publish_video(
                credential=valid_credential,
                video_path=str(video_file),
                title="Tag Test",
                tags=["funny", "viral"],
            )

        assert result.success is True
        assert "#funny" in captured_body.get("text", "")
        assert "#viral" in captured_body.get("text", "")
