"""
Sprint 2: Bilibili 平台服务单元测试。

覆盖 BilibiliService 的 OAuth 流程和视频发布。
"""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from platform_services.bilibili import (  # noqa: E402
    AUTH_URI,
    TOKEN_URI,
    REFRESH_TOKEN_URI,
    BilibiliService,
)
from platform_services.base import (  # noqa: E402
    OAuthCredential,
    PlatformAccount,
    PlatformType,
    PublishResult,
)
from platform_services.exceptions import OAuthError, PublishError  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bilibili_service():
    return BilibiliService(
        client_id="test_bili_client_id",
        client_secret="test_bili_client_secret",
        redirect_uri="http://localhost:9000/api/oauth/callback/bilibili",
    )


@pytest.fixture
def valid_credential():
    return OAuthCredential(
        access_token="bili_access_token_123",
        refresh_token="bili_refresh_token_456",
        expires_at=int(time.time()) + 86400,
    )


@pytest.fixture
def expiring_credential():
    return OAuthCredential(
        access_token="bili_expiring_token",
        refresh_token="bili_refresh_token",
        expires_at=int(time.time()) + 300,  # 5 min left (< 600s buffer)
    )


# ---------------------------------------------------------------------------
# get_auth_url
# ---------------------------------------------------------------------------

class TestGetAuthUrl:
    @pytest.mark.asyncio
    async def test_generates_correct_url(self, bilibili_service):
        url = await bilibili_service.get_auth_url(state="test_state_abc")
        assert url.startswith(AUTH_URI)
        assert "client_id=test_bili_client_id" in url
        assert "state=test_state_abc" in url
        assert "response_type=code" in url

    @pytest.mark.asyncio
    async def test_includes_redirect_uri(self, bilibili_service):
        url = await bilibili_service.get_auth_url(state="s")
        assert "redirect_uri=" in url
        assert "callback" in url


# ---------------------------------------------------------------------------
# handle_callback
# ---------------------------------------------------------------------------

class TestHandleCallback:
    @pytest.mark.asyncio
    async def test_success(self, bilibili_service):
        """成功换取 token + 获取用户信息（mid, name, face）。"""
        token_response = {
            "code": 0,
            "data": {
                "access_token": "bili_new_access",
                "refresh_token": "bili_new_refresh",
                "expires_in": 86400,
            },
        }
        user_response = {
            "code": 0,
            "data": {
                "mid": 12345678,
                "name": "测试用户",
                "face": "https://i0.hdslb.com/avatar.jpg",
            },
        }

        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = token_response

        mock_user_resp = MagicMock()
        mock_user_resp.status_code = 200
        mock_user_resp.json.return_value = user_response

        with patch("platform_services.bilibili.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_token_resp
            mock_client.get.return_value = mock_user_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            account, credential = await bilibili_service.handle_callback(
                code="bili_auth_code", state="state_xyz",
            )

        assert isinstance(account, PlatformAccount)
        assert account.platform == PlatformType.BILIBILI
        assert account.platform_uid == "12345678"
        assert account.nickname == "测试用户"
        assert account.avatar_url == "https://i0.hdslb.com/avatar.jpg"

        assert isinstance(credential, OAuthCredential)
        assert credential.access_token == "bili_new_access"
        assert credential.refresh_token == "bili_new_refresh"
        assert credential.expires_at > time.time()

    @pytest.mark.asyncio
    async def test_token_exchange_http_failure(self, bilibili_service):
        """HTTP 错误时抛出 OAuthError。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch("platform_services.bilibili.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(OAuthError, match="token exchange failed"):
                await bilibili_service.handle_callback(code="bad", state="s")

    @pytest.mark.asyncio
    async def test_token_exchange_api_error(self, bilibili_service):
        """Bilibili API 返回非零 code 时抛出 OAuthError。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": -400, "message": "请求错误"}

        with patch("platform_services.bilibili.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(OAuthError, match="token exchange error"):
                await bilibili_service.handle_callback(code="bad", state="s")

    @pytest.mark.asyncio
    async def test_user_info_failure(self, bilibili_service):
        """获取用户信息失败时抛出 OAuthError。"""
        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {
            "code": 0,
            "data": {
                "access_token": "at",
                "refresh_token": "rt",
                "expires_in": 86400,
            },
        }

        mock_user_resp = MagicMock()
        mock_user_resp.status_code = 200
        mock_user_resp.json.return_value = {"code": -101, "message": "账号未登录"}

        with patch("platform_services.bilibili.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_token_resp
            mock_client.get.return_value = mock_user_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(OAuthError, match="user info error"):
                await bilibili_service.handle_callback(code="c", state="s")


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------

class TestRefreshToken:
    @pytest.mark.asyncio
    async def test_returns_new_refresh_token(self, bilibili_service, valid_credential):
        """Bilibili 刷新后返回新的 refresh_token。"""
        refresh_response = {
            "code": 0,
            "data": {
                "access_token": "bili_refreshed_access",
                "refresh_token": "bili_new_refresh_token",
                "expires_in": 86400,
            },
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = refresh_response

        with patch("platform_services.bilibili.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            new_cred = await bilibili_service.refresh_token(valid_credential)

        assert new_cred.access_token == "bili_refreshed_access"
        assert new_cred.refresh_token == "bili_new_refresh_token"
        assert new_cred.refresh_token != valid_credential.refresh_token
        assert new_cred.expires_at > time.time()

    @pytest.mark.asyncio
    async def test_refresh_http_failure(self, bilibili_service, valid_credential):
        """HTTP 错误时抛出 OAuthError。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        with patch("platform_services.bilibili.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(OAuthError, match="token refresh failed"):
                await bilibili_service.refresh_token(valid_credential)

    @pytest.mark.asyncio
    async def test_refresh_api_error(self, bilibili_service, valid_credential):
        """Bilibili API 返回非零 code 时抛出 OAuthError。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": -101, "message": "refresh_token 已过期"}

        with patch("platform_services.bilibili.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(OAuthError, match="token refresh error"):
                await bilibili_service.refresh_token(valid_credential)


# ---------------------------------------------------------------------------
# check_token_status
# ---------------------------------------------------------------------------

class TestCheckTokenStatus:
    @pytest.mark.asyncio
    async def test_valid_token(self, bilibili_service, valid_credential):
        """有效 token（距过期 > 600s）返回 True。"""
        result = await bilibili_service.check_token_status(valid_credential)
        assert result is True

    @pytest.mark.asyncio
    async def test_expiring_token(self, bilibili_service, expiring_credential):
        """即将过期的 token（距过期 < 600s）返回 False。"""
        result = await bilibili_service.check_token_status(expiring_credential)
        assert result is False

    @pytest.mark.asyncio
    async def test_expired_token(self, bilibili_service):
        """已过期的 token 返回 False。"""
        expired = OAuthCredential(
            access_token="expired",
            refresh_token="rt",
            expires_at=int(time.time()) - 100,
        )
        result = await bilibili_service.check_token_status(expired)
        assert result is False


# ---------------------------------------------------------------------------
# publish_video
# ---------------------------------------------------------------------------

class TestPublishVideo:
    @pytest.mark.asyncio
    async def test_file_not_found(self, bilibili_service, valid_credential):
        """视频文件不存在时抛出 PublishError。"""
        with pytest.raises(PublishError, match="not found"):
            await bilibili_service.publish_video(
                credential=valid_credential,
                video_path="/tmp/nonexistent_video.mp4",
                title="Test",
            )

    @pytest.mark.asyncio
    async def test_successful_upload(self, bilibili_service, valid_credential, tmp_path):
        """成功上传视频（含分片）。"""
        # 创建测试视频文件
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"x" * 1024)

        pre_upload_response = {
            "url": "https://upos-cs.bilivideo.com/upload/123",
            "complete": "https://upos-cs.bilivideo.com/complete/123",
            "bili_filename": "n123456789.mp4",
            "biz_id": 99999,
        }
        submit_response = {
            "code": 0,
            "data": {
                "bvid": "BV1xx411c7mD",
                "aid": 123456,
            },
        }

        mock_pre_resp = MagicMock()
        mock_pre_resp.status_code = 200
        mock_pre_resp.json.return_value = pre_upload_response

        mock_chunk_resp = MagicMock()
        mock_chunk_resp.status_code = 200

        mock_complete_resp = MagicMock()
        mock_complete_resp.status_code = 200

        mock_submit_resp = MagicMock()
        mock_submit_resp.status_code = 200
        mock_submit_resp.json.return_value = submit_response

        with patch("platform_services.bilibili.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()

            # get → pre-upload
            mock_client.get.return_value = mock_pre_resp
            # put → chunk upload
            mock_client.put.return_value = mock_chunk_resp
            # post calls: complete then submit
            mock_client.post.side_effect = [mock_complete_resp, mock_submit_resp]

            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await bilibili_service.publish_video(
                credential=valid_credential,
                video_path=str(video_file),
                title="测试视频",
                description="测试描述",
                tags=["测试", "视频"],
            )

        assert isinstance(result, PublishResult)
        assert result.success is True
        assert result.post_id == "BV1xx411c7mD"
        assert "bilibili.com/video/BV1xx411c7mD" in result.permalink

    @pytest.mark.asyncio
    async def test_pre_upload_failure(self, bilibili_service, valid_credential, tmp_path):
        """pre-upload 失败时抛出 PublishError。"""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"data")

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Server Error"

        with patch("platform_services.bilibili.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(PublishError, match="pre-upload failed"):
                await bilibili_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Test",
                )

    @pytest.mark.asyncio
    async def test_submit_api_error(self, bilibili_service, valid_credential, tmp_path):
        """submit 返回非零 code 时抛出 PublishError。"""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"data")

        mock_pre_resp = MagicMock()
        mock_pre_resp.status_code = 200
        mock_pre_resp.json.return_value = {
            "url": "https://upos.bilivideo.com/upload/123",
            "complete": "",
            "bili_filename": "n123.mp4",
            "biz_id": 1,
        }

        mock_chunk_resp = MagicMock()
        mock_chunk_resp.status_code = 200

        mock_submit_resp = MagicMock()
        mock_submit_resp.status_code = 200
        mock_submit_resp.json.return_value = {"code": -4, "message": "稿件标题不合规"}

        with patch("platform_services.bilibili.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_pre_resp
            mock_client.put.return_value = mock_chunk_resp
            mock_client.post.return_value = mock_submit_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(PublishError, match="submit error"):
                await bilibili_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Test",
                )


# ---------------------------------------------------------------------------
# Service attributes
# ---------------------------------------------------------------------------

class TestServiceAttributes:
    def test_platform_type(self, bilibili_service):
        assert bilibili_service.platform == PlatformType.BILIBILI

    def test_auth_method(self, bilibili_service):
        from platform_services.base import AuthMethod
        assert bilibili_service.auth_method == AuthMethod.OAUTH2
