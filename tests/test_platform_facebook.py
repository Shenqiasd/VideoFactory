"""
Sprint 4: FacebookService 单元测试。

覆盖 OAuth 流程（get_auth_url, handle_callback, refresh_token, check_token_status）
以及视频发布（publish_video — 简单上传 + resumable 上传）。所有 HTTP 调用均使用 mock。
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
from platform_services.facebook import (  # noqa: E402
    GRAPH_API_BASE,
    GRAPH_VIDEO_BASE,
    SCOPES,
    FacebookService,
)
from platform_services.meta_base import AUTH_URI  # noqa: E402


@pytest.fixture
def facebook_service():
    return FacebookService(
        client_id="fb_test_client_id",
        client_secret="fb_test_client_secret",
        redirect_uri="http://localhost:9000/api/oauth/callback/facebook",
    )


@pytest.fixture
def valid_credential():
    return OAuthCredential(
        access_token="fb_page_token_123",
        refresh_token="fb_long_lived_user_token",
        expires_at=int(time.time()) + 5184000,
        raw=json.dumps({"page_id": "page_001", "user_id": "user_001"}),
    )


@pytest.fixture
def expiring_credential():
    return OAuthCredential(
        access_token="fb_expiring_token",
        refresh_token="fb_refresh_token",
        expires_at=int(time.time()) + 300,  # 5 minutes, within 600s buffer
    )


# ---------------------------------------------------------------------------
# Class attributes
# ---------------------------------------------------------------------------

class TestFacebookServiceAttributes:
    def test_platform(self, facebook_service):
        assert facebook_service.platform == PlatformType.FACEBOOK

    def test_auth_method(self, facebook_service):
        assert facebook_service.auth_method == AuthMethod.OAUTH2

    def test_client_config(self, facebook_service):
        assert facebook_service.client_id == "fb_test_client_id"
        assert facebook_service.client_secret == "fb_test_client_secret"

    def test_scopes(self, facebook_service):
        assert "pages_manage_posts" in facebook_service.SCOPES
        assert "pages_read_engagement" in facebook_service.SCOPES
        assert "publish_video" in facebook_service.SCOPES


# ---------------------------------------------------------------------------
# get_auth_url
# ---------------------------------------------------------------------------

class TestGetAuthUrl:
    @pytest.mark.asyncio
    async def test_generates_correct_url(self, facebook_service):
        url = await facebook_service.get_auth_url(state="test_state_fb")
        assert url.startswith(AUTH_URI)
        assert "client_id=fb_test_client_id" in url
        assert "state=test_state_fb" in url
        assert "response_type=code" in url

    @pytest.mark.asyncio
    async def test_includes_correct_scopes(self, facebook_service):
        url = await facebook_service.get_auth_url(state="s")
        assert "pages_manage_posts" in url
        assert "pages_read_engagement" in url
        assert "publish_video" in url

    @pytest.mark.asyncio
    async def test_includes_redirect_uri(self, facebook_service):
        url = await facebook_service.get_auth_url(state="s")
        assert "redirect_uri=" in url
        assert "localhost" in url


# ---------------------------------------------------------------------------
# handle_callback
# ---------------------------------------------------------------------------

class TestHandleCallback:
    def _build_mock_client(self, get_side_effect, post_side_effect=None):
        """Helper to build a mock httpx.AsyncClient with get/post side effects."""
        mock_client = AsyncMock()
        mock_client.get = get_side_effect
        if post_side_effect:
            mock_client.post = post_side_effect
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    @pytest.mark.asyncio
    async def test_successful_callback(self, facebook_service):
        """测试成功的 OAuth 回调：token 交换 → 长期 token → Page token → 账号信息。"""
        # Short-lived token exchange
        short_token_resp = MagicMock(status_code=200)
        short_token_resp.json.return_value = {"access_token": "short_token_123"}

        # Long-lived token exchange
        long_token_resp = MagicMock(status_code=200)
        long_token_resp.json.return_value = {
            "access_token": "long_lived_user_token",
            "expires_in": 5184000,
        }

        # /me endpoint
        me_resp = MagicMock(status_code=200)
        me_resp.json.return_value = {"id": "user_123", "name": "Test User"}

        # Pages endpoint
        pages_resp = MagicMock(status_code=200)
        pages_resp.json.return_value = {
            "data": [
                {
                    "id": "page_456",
                    "name": "Test Page",
                    "access_token": "page_token_789",
                }
            ]
        }

        # Page picture
        pic_resp = MagicMock(status_code=200)
        pic_resp.json.return_value = {
            "data": {"url": "https://fb.com/page_avatar.jpg"}
        }

        get_call_count = {"n": 0}

        async def mock_get(url, **kwargs):
            get_call_count["n"] += 1
            if "/oauth/access_token" in url:
                # Distinguish short vs long-lived by params
                params = kwargs.get("params", {})
                if params.get("grant_type") == "fb_exchange_token":
                    return long_token_resp
                return short_token_resp
            elif "/me" in url and "/accounts" not in url:
                return me_resp
            elif "/accounts" in url:
                return pages_resp
            elif "/picture" in url:
                return pic_resp
            return MagicMock(status_code=404)

        with patch("platform_services.meta_base.httpx.AsyncClient") as MockClient:
            mock_client = self._build_mock_client(mock_get)
            MockClient.return_value = mock_client

            account, credential = await facebook_service.handle_callback(
                code="fb_test_code", state="fb_test_state"
            )

        assert account.platform == PlatformType.FACEBOOK
        assert account.platform_uid == "page_456"
        assert account.nickname == "Test Page"
        assert account.avatar_url == "https://fb.com/page_avatar.jpg"
        assert credential.access_token == "page_token_789"
        assert credential.refresh_token == "long_lived_user_token"

    @pytest.mark.asyncio
    async def test_token_exchange_failure(self, facebook_service):
        """token 交换失败时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        mock_resp = MagicMock(status_code=400, text="invalid_code")

        async def mock_get(url, **kwargs):
            return mock_resp

        with patch("platform_services.meta_base.httpx.AsyncClient") as MockClient:
            mock_client = self._build_mock_client(mock_get)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="token exchange failed"):
                await facebook_service.handle_callback(code="bad", state="s")

    @pytest.mark.asyncio
    async def test_no_pages_found(self, facebook_service):
        """未找到 Page 时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        short_token_resp = MagicMock(status_code=200)
        short_token_resp.json.return_value = {"access_token": "short"}

        long_token_resp = MagicMock(status_code=200)
        long_token_resp.json.return_value = {
            "access_token": "long", "expires_in": 5184000,
        }

        me_resp = MagicMock(status_code=200)
        me_resp.json.return_value = {"id": "user_1"}

        pages_resp = MagicMock(status_code=200)
        pages_resp.json.return_value = {"data": []}

        async def mock_get(url, **kwargs):
            params = kwargs.get("params", {})
            if "/oauth/access_token" in url:
                if params.get("grant_type") == "fb_exchange_token":
                    return long_token_resp
                return short_token_resp
            elif "/me" in url and "/accounts" not in url:
                return me_resp
            elif "/accounts" in url:
                return pages_resp
            return MagicMock(status_code=404)

        with patch("platform_services.meta_base.httpx.AsyncClient") as MockClient:
            mock_client = self._build_mock_client(mock_get)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="未找到关联的 Page"):
                await facebook_service.handle_callback(code="c", state="s")

    @pytest.mark.asyncio
    async def test_user_info_failure(self, facebook_service):
        """获取用户信息失败时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        short_token_resp = MagicMock(status_code=200)
        short_token_resp.json.return_value = {"access_token": "short"}

        long_token_resp = MagicMock(status_code=200)
        long_token_resp.json.return_value = {
            "access_token": "long", "expires_in": 5184000,
        }

        me_resp = MagicMock(status_code=401, text="Unauthorized")

        async def mock_get(url, **kwargs):
            params = kwargs.get("params", {})
            if "/oauth/access_token" in url:
                if params.get("grant_type") == "fb_exchange_token":
                    return long_token_resp
                return short_token_resp
            elif "/me" in url:
                return me_resp
            return MagicMock(status_code=404)

        with patch("platform_services.meta_base.httpx.AsyncClient") as MockClient:
            mock_client = self._build_mock_client(mock_get)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="user info fetch failed"):
                await facebook_service.handle_callback(code="c", state="s")


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------

class TestRefreshToken:
    @pytest.mark.asyncio
    async def test_successful_refresh_with_page_token(self, facebook_service, valid_credential):
        """刷新成功应重新获取 Page Token 并保留 raw 元数据。"""
        # Long-lived token exchange response
        token_resp = MagicMock(status_code=200)
        token_resp.json.return_value = {
            "access_token": "fb_new_user_token",
            "expires_in": 5184000,
        }

        # Pages response with matching page_id from raw
        pages_resp = MagicMock(status_code=200)
        pages_resp.json.return_value = {
            "data": [
                {"id": "page_001", "access_token": "new_page_token_001"},
                {"id": "page_002", "access_token": "new_page_token_002"},
            ]
        }

        async def mock_get(url, **kwargs):
            if "/oauth/access_token" in url:
                return token_resp
            elif "/accounts" in url:
                return pages_resp
            return MagicMock(status_code=404)

        with patch("platform_services.facebook.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            new_cred = await facebook_service.refresh_token(valid_credential)

        # Page token should be for page_001 (matching raw)
        assert new_cred.access_token == "new_page_token_001"
        # User token stored as refresh_token
        assert new_cred.refresh_token == "fb_new_user_token"
        assert new_cred.expires_at > int(time.time())
        # raw should be preserved (not overwritten with token exchange response)
        assert new_cred.raw == valid_credential.raw
        raw_data = json.loads(new_cred.raw)
        assert raw_data["page_id"] == "page_001"
        assert raw_data["user_id"] == "user_001"

    @pytest.mark.asyncio
    async def test_refresh_uses_refresh_token_not_access_token(self, facebook_service, valid_credential):
        """刷新时应使用 refresh_token (user token)，而非 access_token (page token)。"""
        exchanged_tokens = []

        token_resp = MagicMock(status_code=200)
        token_resp.json.return_value = {
            "access_token": "new_user_token",
            "expires_in": 5184000,
        }

        pages_resp = MagicMock(status_code=200)
        pages_resp.json.return_value = {
            "data": [{"id": "page_001", "access_token": "new_page_tok"}]
        }

        async def mock_get(url, **kwargs):
            params = kwargs.get("params", {})
            if "/oauth/access_token" in url:
                exchanged_tokens.append(params.get("fb_exchange_token"))
                return token_resp
            elif "/accounts" in url:
                return pages_resp
            return MagicMock(status_code=404)

        with patch("platform_services.facebook.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            await facebook_service.refresh_token(valid_credential)

        # Should have exchanged the user token (refresh_token), not page token (access_token)
        assert exchanged_tokens[0] == valid_credential.refresh_token
        assert exchanged_tokens[0] != valid_credential.access_token

    @pytest.mark.asyncio
    async def test_refresh_failure(self, facebook_service, valid_credential):
        """刷新失败时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        mock_resp = MagicMock(status_code=400, text="invalid token")

        async def mock_get(url, **kwargs):
            return mock_resp

        with patch("platform_services.facebook.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="long-lived token exchange failed"):
                await facebook_service.refresh_token(valid_credential)


# ---------------------------------------------------------------------------
# check_token_status
# ---------------------------------------------------------------------------

class TestCheckTokenStatus:
    @pytest.mark.asyncio
    async def test_valid_token(self, facebook_service, valid_credential):
        result = await facebook_service.check_token_status(valid_credential)
        assert result is True

    @pytest.mark.asyncio
    async def test_expiring_token(self, facebook_service, expiring_credential):
        result = await facebook_service.check_token_status(expiring_credential)
        assert result is False

    @pytest.mark.asyncio
    async def test_expired_token(self, facebook_service):
        cred = OAuthCredential(
            access_token="expired",
            refresh_token="rt",
            expires_at=int(time.time()) - 100,
        )
        result = await facebook_service.check_token_status(cred)
        assert result is False


# ---------------------------------------------------------------------------
# publish_video — simple upload
# ---------------------------------------------------------------------------

class TestPublishVideoSimple:
    @pytest.mark.asyncio
    async def test_successful_simple_upload(self, facebook_service, valid_credential, tmp_path):
        """测试简单视频上传成功。"""
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 1024)

        upload_resp = MagicMock(status_code=200)
        upload_resp.json.return_value = {"id": "video_001"}

        async def mock_post(url, **kwargs):
            return upload_resp

        with patch("platform_services.facebook.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await facebook_service.publish_video(
                credential=valid_credential,
                video_path=str(video_file),
                title="Test Video",
                description="Test description",
            )

        assert result.success is True
        assert result.post_id == "video_001"
        assert "facebook.com/page_001/videos/video_001" in result.permalink

    @pytest.mark.asyncio
    async def test_upload_api_error(self, facebook_service, valid_credential, tmp_path):
        """上传 API 返回 error 时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "err_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        upload_resp = MagicMock(status_code=200)
        upload_resp.json.return_value = {
            "error": {"message": "Invalid video format"}
        }

        async def mock_post(url, **kwargs):
            return upload_resp

        with patch("platform_services.facebook.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="upload error"):
                await facebook_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Error Video",
                )

    @pytest.mark.asyncio
    async def test_upload_http_failure(self, facebook_service, valid_credential, tmp_path):
        """上传 HTTP 失败时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "fail_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        upload_resp = MagicMock(status_code=500, text="Server Error")

        async def mock_post(url, **kwargs):
            return upload_resp

        with patch("platform_services.facebook.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="upload failed"):
                await facebook_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Fail Video",
                )

    @pytest.mark.asyncio
    async def test_missing_page_id(self, facebook_service, tmp_path):
        """缺少 page_id 时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "no_page.mp4"
        video_file.write_bytes(b"\x00" * 512)

        cred = OAuthCredential(
            access_token="token",
            refresh_token="rt",
            expires_at=int(time.time()) + 3600,
            raw="{}",
        )

        with pytest.raises(PublishError, match="missing page_id"):
            await facebook_service.publish_video(
                credential=cred,
                video_path=str(video_file),
                title="No Page",
            )

    @pytest.mark.asyncio
    async def test_page_id_from_platform_options(self, facebook_service, tmp_path):
        """通过 platform_options 传入 page_id。"""
        video_file = tmp_path / "opts_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        cred = OAuthCredential(
            access_token="token",
            refresh_token="rt",
            expires_at=int(time.time()) + 3600,
            raw="{}",
        )

        upload_resp = MagicMock(status_code=200)
        upload_resp.json.return_value = {"id": "video_opt"}

        post_urls = []

        async def mock_post(url, **kwargs):
            post_urls.append(url)
            return upload_resp

        with patch("platform_services.facebook.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await facebook_service.publish_video(
                credential=cred,
                video_path=str(video_file),
                title="Opts Video",
                page_id="custom_page_99",
            )

        assert result.success is True
        assert "custom_page_99" in post_urls[0]


# ---------------------------------------------------------------------------
# publish_video — resumable upload
# ---------------------------------------------------------------------------

class TestPublishVideoResumable:
    @pytest.mark.asyncio
    async def test_successful_resumable_upload(self, facebook_service, valid_credential, tmp_path):
        """测试 resumable 上传流程: init → transfer → finish。"""
        from platform_services.facebook import SIMPLE_UPLOAD_LIMIT

        video_file = tmp_path / "big_video.mp4"
        # 创建一个超过简单上传限制的文件（使用 mock 来控制大小）
        video_file.write_bytes(b"\x00" * 1024)

        init_resp = MagicMock(status_code=200)
        init_resp.json.return_value = {
            "upload_session_id": "session_abc",
            "start_offset": "0",
            "end_offset": "1024",
        }

        transfer_resp = MagicMock(status_code=200)
        transfer_resp.json.return_value = {
            "start_offset": "1024",
            "end_offset": "1024",
        }

        finish_resp = MagicMock(status_code=200)
        finish_resp.json.return_value = {"video_id": "video_resumable_001"}

        call_log = []

        async def mock_post(url, **kwargs):
            data = kwargs.get("data", {})
            phase = data.get("upload_phase", "")
            call_log.append(phase)
            if phase == "start":
                return init_resp
            elif phase == "transfer":
                return transfer_resp
            elif phase == "finish":
                return finish_resp
            return MagicMock(status_code=400)

        with patch("platform_services.facebook.httpx.AsyncClient") as MockClient, \
             patch("platform_services.facebook.SIMPLE_UPLOAD_LIMIT", 512):
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await facebook_service.publish_video(
                credential=valid_credential,
                video_path=str(video_file),
                title="Big Video",
                description="Resumable test",
            )

        assert result.success is True
        assert result.post_id == "video_resumable_001"
        assert "start" in call_log
        assert "transfer" in call_log
        assert "finish" in call_log

    @pytest.mark.asyncio
    async def test_resumable_init_failure(self, facebook_service, valid_credential, tmp_path):
        """resumable 初始化失败时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "fail_big.mp4"
        video_file.write_bytes(b"\x00" * 1024)

        init_resp = MagicMock(status_code=500, text="Server Error")

        async def mock_post(url, **kwargs):
            return init_resp

        with patch("platform_services.facebook.httpx.AsyncClient") as MockClient, \
             patch("platform_services.facebook.SIMPLE_UPLOAD_LIMIT", 512):
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="resumable init failed"):
                await facebook_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Fail Big",
                )
