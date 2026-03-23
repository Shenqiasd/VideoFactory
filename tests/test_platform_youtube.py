"""
Sprint 2: YouTube 平台服务单元测试。

覆盖 YouTubeService 的 OAuth 流程和视频发布。
"""

import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from platform_services.youtube import (  # noqa: E402
    AUTH_URI,
    CHANNEL_API,
    SCOPES,
    TOKEN_URI,
    YouTubeService,
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
def youtube_service():
    return YouTubeService(
        client_id="test_client_id",
        client_secret="test_client_secret",
        redirect_uri="http://localhost:9000/api/oauth/callback/youtube",
    )


@pytest.fixture
def valid_credential():
    return OAuthCredential(
        access_token="ya29.test_access_token",
        refresh_token="1//test_refresh_token",
        expires_at=int(time.time()) + 3600,
    )


@pytest.fixture
def expiring_credential():
    return OAuthCredential(
        access_token="ya29.expiring_token",
        refresh_token="1//test_refresh_token",
        expires_at=int(time.time()) + 300,  # 5 min left (< 600s buffer)
    )


# ---------------------------------------------------------------------------
# get_auth_url
# ---------------------------------------------------------------------------

class TestGetAuthUrl:
    @pytest.mark.asyncio
    async def test_generates_correct_url(self, youtube_service):
        url = await youtube_service.get_auth_url(state="test_state_123")
        assert url.startswith(AUTH_URI)
        assert "client_id=test_client_id" in url
        assert "state=test_state_123" in url
        assert "response_type=code" in url
        assert "access_type=offline" in url
        assert "prompt=consent" in url

    @pytest.mark.asyncio
    async def test_includes_all_scopes(self, youtube_service):
        url = await youtube_service.get_auth_url(state="s")
        for scope_part in ["youtube.upload", "youtube.readonly", "userinfo.profile"]:
            assert scope_part in url

    @pytest.mark.asyncio
    async def test_includes_redirect_uri(self, youtube_service):
        url = await youtube_service.get_auth_url(state="s")
        assert "redirect_uri=" in url
        assert "callback" in url


# ---------------------------------------------------------------------------
# handle_callback
# ---------------------------------------------------------------------------

class TestHandleCallback:
    @pytest.mark.asyncio
    async def test_success(self, youtube_service):
        """成功换取 token + 获取频道信息。"""
        token_response = {
            "access_token": "ya29.new_access",
            "refresh_token": "1//new_refresh",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        channel_response = {
            "items": [
                {
                    "id": "UC_test_channel_id",
                    "snippet": {
                        "title": "Test Channel",
                        "customUrl": "@testchannel",
                        "thumbnails": {
                            "default": {"url": "https://yt.com/avatar.jpg"},
                        },
                    },
                }
            ]
        }

        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = token_response

        mock_channel_resp = MagicMock()
        mock_channel_resp.status_code = 200
        mock_channel_resp.json.return_value = channel_response

        with patch("platform_services.youtube.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_token_resp
            mock_client.get.return_value = mock_channel_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            account, credential = await youtube_service.handle_callback(
                code="auth_code_123", state="state_abc",
            )

        assert isinstance(account, PlatformAccount)
        assert account.platform == PlatformType.YOUTUBE
        assert account.platform_uid == "UC_test_channel_id"
        assert account.nickname == "Test Channel"
        assert account.username == "@testchannel"
        assert account.avatar_url == "https://yt.com/avatar.jpg"

        assert isinstance(credential, OAuthCredential)
        assert credential.access_token == "ya29.new_access"
        assert credential.refresh_token == "1//new_refresh"
        assert credential.expires_at > time.time()

    @pytest.mark.asyncio
    async def test_token_exchange_failure(self, youtube_service):
        """token 换取失败抛出 OAuthError。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "invalid_grant"

        with patch("platform_services.youtube.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(OAuthError, match="token exchange failed"):
                await youtube_service.handle_callback(code="bad", state="s")

    @pytest.mark.asyncio
    async def test_no_channel_found(self, youtube_service):
        """没有频道时抛出 OAuthError。"""
        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {
            "access_token": "ya29.test",
            "refresh_token": "rt",
            "expires_in": 3600,
        }

        mock_channel_resp = MagicMock()
        mock_channel_resp.status_code = 200
        mock_channel_resp.json.return_value = {"items": []}

        with patch("platform_services.youtube.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_token_resp
            mock_client.get.return_value = mock_channel_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(OAuthError, match="No YouTube channel"):
                await youtube_service.handle_callback(code="c", state="s")


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------

class TestRefreshToken:
    @pytest.mark.asyncio
    async def test_preserves_original_refresh_token(self, youtube_service, valid_credential):
        """刷新后保留原始的 refresh_token（Google 不返回新的）。"""
        refresh_response = {
            "access_token": "ya29.refreshed_token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = refresh_response

        with patch("platform_services.youtube.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            new_cred = await youtube_service.refresh_token(valid_credential)

        assert new_cred.access_token == "ya29.refreshed_token"
        assert new_cred.refresh_token == valid_credential.refresh_token
        assert new_cred.expires_at > time.time()

    @pytest.mark.asyncio
    async def test_refresh_failure(self, youtube_service, valid_credential):
        """刷新失败抛出 OAuthError。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "invalid_token"

        with patch("platform_services.youtube.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(OAuthError, match="token refresh failed"):
                await youtube_service.refresh_token(valid_credential)


# ---------------------------------------------------------------------------
# check_token_status
# ---------------------------------------------------------------------------

class TestCheckTokenStatus:
    @pytest.mark.asyncio
    async def test_valid_token(self, youtube_service, valid_credential):
        """有效 token（距过期 > 600s）返回 True。"""
        result = await youtube_service.check_token_status(valid_credential)
        assert result is True

    @pytest.mark.asyncio
    async def test_expiring_token(self, youtube_service, expiring_credential):
        """即将过期的 token（距过期 < 600s）返回 False。"""
        result = await youtube_service.check_token_status(expiring_credential)
        assert result is False

    @pytest.mark.asyncio
    async def test_expired_token(self, youtube_service):
        """已过期的 token 返回 False。"""
        expired = OAuthCredential(
            access_token="expired",
            refresh_token="rt",
            expires_at=int(time.time()) - 100,
        )
        result = await youtube_service.check_token_status(expired)
        assert result is False


# ---------------------------------------------------------------------------
# publish_video
# ---------------------------------------------------------------------------

class TestPublishVideo:
    @pytest.mark.asyncio
    async def test_successful_upload(self, youtube_service, valid_credential):
        """成功上传视频。"""
        mock_insert_response = {"id": "dQw4w9WgXcQ"}

        mock_request = MagicMock()
        mock_request.next_chunk.return_value = (None, mock_insert_response)

        mock_videos = MagicMock()
        mock_videos.insert.return_value = mock_request

        mock_youtube = MagicMock()
        mock_youtube.videos.return_value = mock_videos

        with patch("googleapiclient.http.MediaFileUpload") as mock_mfu, \
             patch("googleapiclient.discovery.build", return_value=mock_youtube), \
             patch("google.oauth2.credentials.Credentials"):

            result = await youtube_service.publish_video(
                credential=valid_credential,
                video_path="/tmp/test_video.mp4",
                title="Test Video",
                description="A test upload",
                tags=["test", "video"],
            )

        assert isinstance(result, PublishResult)
        assert result.success is True
        assert result.post_id == "dQw4w9WgXcQ"
        assert "youtube.com/watch?v=dQw4w9WgXcQ" in result.permalink

    @pytest.mark.asyncio
    async def test_upload_with_progress(self, youtube_service, valid_credential):
        """带进度的分块上传。"""
        mock_status = MagicMock()
        mock_status.progress.return_value = 0.5

        mock_request = MagicMock()
        mock_request.next_chunk.side_effect = [
            (mock_status, None),  # first chunk: 50%
            (None, {"id": "video123"}),  # final chunk: done
        ]

        mock_videos = MagicMock()
        mock_videos.insert.return_value = mock_request

        mock_youtube = MagicMock()
        mock_youtube.videos.return_value = mock_videos

        with patch("googleapiclient.http.MediaFileUpload"), \
             patch("googleapiclient.discovery.build", return_value=mock_youtube), \
             patch("google.oauth2.credentials.Credentials"):

            result = await youtube_service.publish_video(
                credential=valid_credential,
                video_path="/tmp/test_video.mp4",
                title="Test",
            )

        assert result.success is True
        assert result.post_id == "video123"

    @pytest.mark.asyncio
    async def test_upload_failure(self, youtube_service, valid_credential):
        """上传失败抛出 PublishError。"""
        mock_request = MagicMock()
        mock_request.next_chunk.side_effect = Exception("Upload timeout")

        mock_videos = MagicMock()
        mock_videos.insert.return_value = mock_request

        mock_youtube = MagicMock()
        mock_youtube.videos.return_value = mock_videos

        with patch("googleapiclient.http.MediaFileUpload"), \
             patch("googleapiclient.discovery.build", return_value=mock_youtube), \
             patch("google.oauth2.credentials.Credentials"):

            with pytest.raises(PublishError, match="Upload timeout"):
                await youtube_service.publish_video(
                    credential=valid_credential,
                    video_path="/tmp/test.mp4",
                    title="Test",
                )


# ---------------------------------------------------------------------------
# Service attributes
# ---------------------------------------------------------------------------

class TestServiceAttributes:
    def test_platform_type(self, youtube_service):
        assert youtube_service.platform == PlatformType.YOUTUBE

    def test_auth_method(self, youtube_service):
        from platform_services.base import AuthMethod
        assert youtube_service.auth_method == AuthMethod.OAUTH2
