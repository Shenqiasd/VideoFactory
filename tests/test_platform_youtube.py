"""
Sprint 2: YouTubeService 单元测试。

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
from platform_services.youtube import (  # noqa: E402
    AUTH_URI,
    SCOPES,
    TOKEN_URI,
    YouTubeService,
)


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
        expires_at=int(time.time()) + 300,  # 5 minutes, within 600s buffer
    )


# ---------------------------------------------------------------------------
# Class attributes
# ---------------------------------------------------------------------------

class TestYouTubeServiceAttributes:
    def test_platform(self, youtube_service):
        assert youtube_service.platform == PlatformType.YOUTUBE

    def test_auth_method(self, youtube_service):
        assert youtube_service.auth_method == AuthMethod.OAUTH2

    def test_client_config(self, youtube_service):
        assert youtube_service.client_id == "test_client_id"
        assert youtube_service.client_secret == "test_client_secret"


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
        assert "localhost" in url


# ---------------------------------------------------------------------------
# handle_callback
# ---------------------------------------------------------------------------

class TestHandleCallback:
    @pytest.mark.asyncio
    async def test_successful_callback(self, youtube_service):
        """测试成功的 OAuth 回调：token 交换 + 频道信息获取。"""
        token_response = {
            "access_token": "ya29.new_token",
            "refresh_token": "1//refresh",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        channel_response = {
            "items": [
                {
                    "id": "UC_test_channel",
                    "snippet": {
                        "title": "Test Channel",
                        "customUrl": "@testchannel",
                        "thumbnails": {
                            "default": {"url": "https://yt.com/avatar.jpg"}
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

        async def mock_post(url, **kwargs):
            return mock_token_resp

        async def mock_get(url, **kwargs):
            return mock_channel_resp

        with patch("platform_services.youtube.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            account, credential = await youtube_service.handle_callback(
                code="test_code", state="test_state"
            )

        assert account.platform == PlatformType.YOUTUBE
        assert account.platform_uid == "UC_test_channel"
        assert account.nickname == "Test Channel"
        assert account.username == "@testchannel"
        assert account.avatar_url == "https://yt.com/avatar.jpg"
        assert credential.access_token == "ya29.new_token"
        assert credential.refresh_token == "1//refresh"

    @pytest.mark.asyncio
    async def test_token_exchange_failure(self, youtube_service):
        """token 交换失败时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "invalid_grant"

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.youtube.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="token exchange failed"):
                await youtube_service.handle_callback(code="bad", state="s")

    @pytest.mark.asyncio
    async def test_no_channel_found(self, youtube_service):
        """未找到频道时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 3600,
        }

        mock_channel_resp = MagicMock()
        mock_channel_resp.status_code = 200
        mock_channel_resp.json.return_value = {"items": []}

        async def mock_post(url, **kwargs):
            return mock_token_resp

        async def mock_get(url, **kwargs):
            return mock_channel_resp

        with patch("platform_services.youtube.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="未找到关联的频道"):
                await youtube_service.handle_callback(code="c", state="s")


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------

class TestRefreshToken:
    @pytest.mark.asyncio
    async def test_preserves_original_refresh_token(self, youtube_service, valid_credential):
        """刷新后应保留原始 refresh_token（Google 不返回新 refresh_token）。"""
        refresh_response = {
            "access_token": "ya29.refreshed_token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = refresh_response

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.youtube.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            new_credential = await youtube_service.refresh_token(valid_credential)

        assert new_credential.access_token == "ya29.refreshed_token"
        assert new_credential.refresh_token == valid_credential.refresh_token
        assert new_credential.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_refresh_failure(self, youtube_service, valid_credential):
        """刷新失败时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "invalid_grant"

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.youtube.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="refresh failed"):
                await youtube_service.refresh_token(valid_credential)


# ---------------------------------------------------------------------------
# check_token_status
# ---------------------------------------------------------------------------

class TestCheckTokenStatus:
    @pytest.mark.asyncio
    async def test_valid_token(self, youtube_service, valid_credential):
        """距离过期 > 600s 的 token 应返回 True。"""
        result = await youtube_service.check_token_status(valid_credential)
        assert result is True

    @pytest.mark.asyncio
    async def test_expiring_token(self, youtube_service, expiring_credential):
        """距离过期 < 600s 的 token 应返回 False。"""
        result = await youtube_service.check_token_status(expiring_credential)
        assert result is False

    @pytest.mark.asyncio
    async def test_expired_token(self, youtube_service):
        """已过期 token 应返回 False。"""
        cred = OAuthCredential(
            access_token="expired",
            refresh_token="rt",
            expires_at=int(time.time()) - 100,
        )
        result = await youtube_service.check_token_status(cred)
        assert result is False


# ---------------------------------------------------------------------------
# publish_video
# ---------------------------------------------------------------------------

class TestPublishVideo:
    @pytest.mark.asyncio
    async def test_successful_upload(self, youtube_service, valid_credential, tmp_path):
        """使用 mocked Google API client 测试成功上传。"""
        # 创建临时视频文件
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 1024)

        mock_response = {"id": "dQw4w9WgXcQ", "status": {"uploadStatus": "uploaded"}}

        mock_request = MagicMock()
        mock_request.next_chunk.return_value = (None, mock_response)

        mock_videos = MagicMock()
        mock_videos.insert.return_value = mock_request

        mock_youtube = MagicMock()
        mock_youtube.videos.return_value = mock_videos

        with patch("platform_services.youtube.build", return_value=mock_youtube), \
             patch("platform_services.youtube.MediaFileUpload") as MockMedia, \
             patch("platform_services.youtube.Credentials"):
            MockMedia.return_value = MagicMock()

            result = await youtube_service.publish_video(
                credential=valid_credential,
                video_path=str(video_file),
                title="Test Video",
                description="Test description",
                tags=["test", "video"],
            )

        assert result.success is True
        assert result.post_id == "dQw4w9WgXcQ"
        assert "youtube.com/watch?v=dQw4w9WgXcQ" in result.permalink

    @pytest.mark.asyncio
    async def test_upload_with_progress(self, youtube_service, valid_credential, tmp_path):
        """测试 resumable upload 包含进度回调。"""
        video_file = tmp_path / "big_video.mp4"
        video_file.write_bytes(b"\x00" * 2048)

        mock_status = MagicMock()
        mock_status.progress.return_value = 0.5

        mock_request = MagicMock()
        mock_request.next_chunk.side_effect = [
            (mock_status, None),     # first chunk: progress
            (None, {"id": "v123"}),  # second chunk: done
        ]

        mock_videos = MagicMock()
        mock_videos.insert.return_value = mock_request

        mock_youtube = MagicMock()
        mock_youtube.videos.return_value = mock_videos

        with patch("platform_services.youtube.build", return_value=mock_youtube), \
             patch("platform_services.youtube.MediaFileUpload"), \
             patch("platform_services.youtube.Credentials"):
            result = await youtube_service.publish_video(
                credential=valid_credential,
                video_path=str(video_file),
                title="Big Video",
            )

        assert result.success is True
        assert result.post_id == "v123"

    @pytest.mark.asyncio
    async def test_upload_failure(self, youtube_service, valid_credential, tmp_path):
        """上传失败时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "fail_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        mock_request = MagicMock()
        mock_request.next_chunk.side_effect = Exception("Network error")

        mock_videos = MagicMock()
        mock_videos.insert.return_value = mock_request

        mock_youtube = MagicMock()
        mock_youtube.videos.return_value = mock_videos

        with patch("platform_services.youtube.build", return_value=mock_youtube), \
             patch("platform_services.youtube.MediaFileUpload"), \
             patch("platform_services.youtube.Credentials"):
            with pytest.raises(PublishError, match="上传失败"):
                await youtube_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Fail Video",
                )

    @pytest.mark.asyncio
    async def test_platform_options(self, youtube_service, valid_credential, tmp_path):
        """测试 category_id 和 privacy_status 通过 platform_options 传递。"""
        video_file = tmp_path / "opts_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        mock_request = MagicMock()
        mock_request.next_chunk.return_value = (None, {"id": "opt123"})

        mock_videos = MagicMock()
        mock_videos.insert.return_value = mock_request

        mock_youtube = MagicMock()
        mock_youtube.videos.return_value = mock_videos

        with patch("platform_services.youtube.build", return_value=mock_youtube), \
             patch("platform_services.youtube.MediaFileUpload"), \
             patch("platform_services.youtube.Credentials"):
            result = await youtube_service.publish_video(
                credential=valid_credential,
                video_path=str(video_file),
                title="Options Video",
                category_id="24",
                privacy_status="public",
            )

        assert result.success is True
        # Verify insert was called with correct body
        call_kwargs = mock_videos.insert.call_args
        body = call_kwargs.kwargs.get("body") or call_kwargs[1].get("body")
        assert body["snippet"]["categoryId"] == "24"
        assert body["status"]["privacyStatus"] == "public"
