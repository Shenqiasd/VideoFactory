"""
Sprint 4: InstagramService 单元测试。

覆盖 OAuth 流程（get_auth_url, handle_callback, refresh_token, check_token_status）
以及视频发布（容器模式：create → poll → publish）。所有 HTTP 调用均使用 mock。
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
from platform_services.instagram import (  # noqa: E402
    GRAPH_API_BASE,
    SCOPES,
    InstagramService,
)
from platform_services.meta_base import AUTH_URI  # noqa: E402


@pytest.fixture
def instagram_service():
    return InstagramService(
        client_id="ig_test_client_id",
        client_secret="ig_test_client_secret",
        redirect_uri="http://localhost:9000/api/oauth/callback/instagram",
    )


@pytest.fixture
def valid_credential():
    return OAuthCredential(
        access_token="ig_long_lived_token_123",
        refresh_token="ig_long_lived_token_123",
        expires_at=int(time.time()) + 5184000,
        raw=json.dumps({
            "ig_user_id": "ig_user_001",
            "page_id": "page_001",
            "user_id": "user_001",
        }),
    )


@pytest.fixture
def expiring_credential():
    return OAuthCredential(
        access_token="ig_expiring_token",
        refresh_token="ig_refresh_token",
        expires_at=int(time.time()) + 300,
    )


# ---------------------------------------------------------------------------
# Class attributes
# ---------------------------------------------------------------------------

class TestInstagramServiceAttributes:
    def test_platform(self, instagram_service):
        assert instagram_service.platform == PlatformType.INSTAGRAM

    def test_auth_method(self, instagram_service):
        assert instagram_service.auth_method == AuthMethod.OAUTH2

    def test_client_config(self, instagram_service):
        assert instagram_service.client_id == "ig_test_client_id"
        assert instagram_service.client_secret == "ig_test_client_secret"

    def test_scopes(self, instagram_service):
        assert "instagram_basic" in instagram_service.SCOPES
        assert "instagram_content_publish" in instagram_service.SCOPES


# ---------------------------------------------------------------------------
# get_auth_url
# ---------------------------------------------------------------------------

class TestGetAuthUrl:
    @pytest.mark.asyncio
    async def test_generates_correct_url(self, instagram_service):
        url = await instagram_service.get_auth_url(state="test_state_ig")
        assert url.startswith(AUTH_URI)
        assert "client_id=ig_test_client_id" in url
        assert "state=test_state_ig" in url
        assert "response_type=code" in url

    @pytest.mark.asyncio
    async def test_includes_correct_scopes(self, instagram_service):
        url = await instagram_service.get_auth_url(state="s")
        assert "instagram_basic" in url
        assert "instagram_content_publish" in url

    @pytest.mark.asyncio
    async def test_includes_redirect_uri(self, instagram_service):
        url = await instagram_service.get_auth_url(state="s")
        assert "redirect_uri=" in url
        assert "localhost" in url


# ---------------------------------------------------------------------------
# handle_callback
# ---------------------------------------------------------------------------

class TestHandleCallback:
    @pytest.mark.asyncio
    async def test_successful_callback(self, instagram_service):
        """测试成功的 OAuth 回调：token → Page → IG Business Account → 账号信息。"""
        short_token_resp = MagicMock(status_code=200)
        short_token_resp.json.return_value = {"access_token": "short_token"}

        long_token_resp = MagicMock(status_code=200)
        long_token_resp.json.return_value = {
            "access_token": "ig_long_lived_token",
            "expires_in": 5184000,
        }

        me_resp = MagicMock(status_code=200)
        me_resp.json.return_value = {"id": "user_789"}

        pages_resp = MagicMock(status_code=200)
        pages_resp.json.return_value = {
            "data": [
                {
                    "id": "page_111",
                    "name": "IG Page",
                    "access_token": "page_token_222",
                }
            ]
        }

        ig_account_resp = MagicMock(status_code=200)
        ig_account_resp.json.return_value = {
            "instagram_business_account": {"id": "ig_biz_333"},
        }

        ig_info_resp = MagicMock(status_code=200)
        ig_info_resp.json.return_value = {
            "id": "ig_biz_333",
            "username": "testinsta",
            "name": "Test Insta",
            "profile_picture_url": "https://ig.com/avatar.jpg",
        }

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
            elif "page_111" in url and "instagram_business_account" in params.get("fields", ""):
                return ig_account_resp
            elif "ig_biz_333" in url:
                return ig_info_resp
            return MagicMock(status_code=404)

        with patch("platform_services.meta_base.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            account, credential = await instagram_service.handle_callback(
                code="ig_code", state="ig_state"
            )

        assert account.platform == PlatformType.INSTAGRAM
        assert account.platform_uid == "ig_biz_333"
        assert account.username == "testinsta"
        assert account.nickname == "Test Insta"
        assert account.avatar_url == "https://ig.com/avatar.jpg"
        assert credential.access_token == "ig_long_lived_token"

    @pytest.mark.asyncio
    async def test_token_exchange_failure(self, instagram_service):
        """token 交换失败时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        mock_resp = MagicMock(status_code=400, text="bad_code")

        async def mock_get(url, **kwargs):
            return mock_resp

        with patch("platform_services.meta_base.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="token exchange failed"):
                await instagram_service.handle_callback(code="bad", state="s")

    @pytest.mark.asyncio
    async def test_no_pages_found(self, instagram_service):
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
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="未找到关联的 Facebook Page"):
                await instagram_service.handle_callback(code="c", state="s")

    @pytest.mark.asyncio
    async def test_no_ig_business_account(self, instagram_service):
        """Page 未关联 IG Business Account 时应抛出 OAuthError。"""
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
        pages_resp.json.return_value = {
            "data": [{"id": "page_1", "name": "P", "access_token": "pt"}]
        }

        ig_resp = MagicMock(status_code=200)
        ig_resp.json.return_value = {}  # No instagram_business_account

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
            elif "page_1" in url:
                return ig_resp
            return MagicMock(status_code=404)

        with patch("platform_services.meta_base.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="未关联 Instagram Business"):
                await instagram_service.handle_callback(code="c", state="s")


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------

class TestRefreshToken:
    @pytest.mark.asyncio
    async def test_successful_refresh(self, instagram_service, valid_credential):
        refresh_resp = MagicMock(status_code=200)
        refresh_resp.json.return_value = {
            "access_token": "ig_refreshed_token",
            "expires_in": 5184000,
        }

        async def mock_get(url, **kwargs):
            return refresh_resp

        with patch("platform_services.meta_base.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            new_cred = await instagram_service.refresh_token(valid_credential)

        assert new_cred.access_token == "ig_refreshed_token"
        assert new_cred.expires_at > int(time.time())
        # raw should be preserved (not overwritten with token exchange response)
        assert new_cred.raw == valid_credential.raw
        raw_data = json.loads(new_cred.raw)
        assert raw_data["ig_user_id"] == "ig_user_001"
        assert raw_data["page_id"] == "page_001"

    @pytest.mark.asyncio
    async def test_refresh_uses_refresh_token(self, instagram_service, valid_credential):
        """刷新时应使用 refresh_token，而非 access_token。"""
        exchanged_tokens = []

        refresh_resp = MagicMock(status_code=200)
        refresh_resp.json.return_value = {
            "access_token": "ig_new_token",
            "expires_in": 5184000,
        }

        async def mock_get(url, **kwargs):
            params = kwargs.get("params", {})
            if "/oauth/access_token" in url:
                exchanged_tokens.append(params.get("fb_exchange_token"))
            return refresh_resp

        with patch("platform_services.meta_base.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            await instagram_service.refresh_token(valid_credential)

        assert exchanged_tokens[0] == valid_credential.refresh_token

    @pytest.mark.asyncio
    async def test_refresh_failure(self, instagram_service, valid_credential):
        from platform_services.exceptions import OAuthError

        mock_resp = MagicMock(status_code=400, text="invalid token")

        async def mock_get(url, **kwargs):
            return mock_resp

        with patch("platform_services.meta_base.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="long-lived token exchange failed"):
                await instagram_service.refresh_token(valid_credential)


# ---------------------------------------------------------------------------
# check_token_status
# ---------------------------------------------------------------------------

class TestCheckTokenStatus:
    @pytest.mark.asyncio
    async def test_valid_token(self, instagram_service, valid_credential):
        result = await instagram_service.check_token_status(valid_credential)
        assert result is True

    @pytest.mark.asyncio
    async def test_expiring_token(self, instagram_service, expiring_credential):
        result = await instagram_service.check_token_status(expiring_credential)
        assert result is False

    @pytest.mark.asyncio
    async def test_expired_token(self, instagram_service):
        cred = OAuthCredential(
            access_token="expired",
            refresh_token="rt",
            expires_at=int(time.time()) - 100,
        )
        result = await instagram_service.check_token_status(cred)
        assert result is False


# ---------------------------------------------------------------------------
# publish_video — container workflow
# ---------------------------------------------------------------------------

class TestPublishVideo:
    @pytest.mark.asyncio
    async def test_successful_container_workflow(self, instagram_service, valid_credential):
        """测试完整的容器流程: create → poll (FINISHED) → publish。"""
        # Create container
        create_resp = MagicMock(status_code=200)
        create_resp.json.return_value = {"id": "container_001"}

        # Poll status — return FINISHED immediately
        poll_resp = MagicMock(status_code=200)
        poll_resp.json.return_value = {"status_code": "FINISHED"}

        # Publish
        publish_resp = MagicMock(status_code=200)
        publish_resp.json.return_value = {"id": "post_001"}

        async def mock_post(url, **kwargs):
            if "/media_publish" in url:
                return publish_resp
            elif "/media" in url:
                return create_resp
            return MagicMock(status_code=404)

        async def mock_get(url, **kwargs):
            return poll_resp

        with patch("platform_services.instagram.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await instagram_service.publish_video(
                credential=valid_credential,
                video_path="/fake/video.mp4",
                title="Test Reel",
                description="Test caption",
                video_url="https://cdn.example.com/video.mp4",
            )

        assert result.success is True
        assert result.post_id == "post_001"
        assert "instagram.com/p/post_001" in result.permalink

    @pytest.mark.asyncio
    async def test_container_poll_in_progress_then_finished(self, instagram_service, valid_credential):
        """测试容器先 IN_PROGRESS 再 FINISHED 的轮询。"""
        create_resp = MagicMock(status_code=200)
        create_resp.json.return_value = {"id": "container_002"}

        poll_count = {"n": 0}

        poll_in_progress = MagicMock(status_code=200)
        poll_in_progress.json.return_value = {"status_code": "IN_PROGRESS"}

        poll_finished = MagicMock(status_code=200)
        poll_finished.json.return_value = {"status_code": "FINISHED"}

        publish_resp = MagicMock(status_code=200)
        publish_resp.json.return_value = {"id": "post_002"}

        async def mock_post(url, **kwargs):
            if "/media_publish" in url:
                return publish_resp
            elif "/media" in url:
                return create_resp
            return MagicMock(status_code=404)

        async def mock_get(url, **kwargs):
            poll_count["n"] += 1
            if poll_count["n"] <= 2:
                return poll_in_progress
            return poll_finished

        with patch("platform_services.instagram.httpx.AsyncClient") as MockClient, \
             patch("platform_services.instagram.asyncio.sleep", new_callable=AsyncMock):
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await instagram_service.publish_video(
                credential=valid_credential,
                video_path="/fake/video.mp4",
                title="Poll Test",
                video_url="https://cdn.example.com/video.mp4",
                poll_interval=0,
            )

        assert result.success is True
        assert result.post_id == "post_002"
        assert poll_count["n"] == 3

    @pytest.mark.asyncio
    async def test_container_error_status(self, instagram_service, valid_credential):
        """容器状态为 ERROR 时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        create_resp = MagicMock(status_code=200)
        create_resp.json.return_value = {"id": "container_err"}

        poll_resp = MagicMock(status_code=200)
        poll_resp.json.return_value = {"status_code": "ERROR"}

        async def mock_post(url, **kwargs):
            return create_resp

        async def mock_get(url, **kwargs):
            return poll_resp

        with patch("platform_services.instagram.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="container processing failed"):
                await instagram_service.publish_video(
                    credential=valid_credential,
                    video_path="/fake/video.mp4",
                    title="Error Reel",
                    video_url="https://cdn.example.com/video.mp4",
                )

    @pytest.mark.asyncio
    async def test_container_poll_timeout(self, instagram_service, valid_credential):
        """容器轮询超时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        create_resp = MagicMock(status_code=200)
        create_resp.json.return_value = {"id": "container_timeout"}

        poll_resp = MagicMock(status_code=200)
        poll_resp.json.return_value = {"status_code": "IN_PROGRESS"}

        async def mock_post(url, **kwargs):
            return create_resp

        async def mock_get(url, **kwargs):
            return poll_resp

        with patch("platform_services.instagram.httpx.AsyncClient") as MockClient, \
             patch("platform_services.instagram.asyncio.sleep", new_callable=AsyncMock), \
             patch("platform_services.instagram.time.time") as mock_time:
            # Simulate time passing beyond timeout
            mock_time.side_effect = [
                1000,    # start_time in _poll_container_status
                1000,    # first elapsed check
                1400,    # second elapsed (beyond 300s timeout)
            ]

            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="polling timeout"):
                await instagram_service.publish_video(
                    credential=valid_credential,
                    video_path="/fake/video.mp4",
                    title="Timeout Reel",
                    video_url="https://cdn.example.com/video.mp4",
                    poll_timeout=300,
                )

    @pytest.mark.asyncio
    async def test_container_creation_failure(self, instagram_service, valid_credential):
        """容器创建失败应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        create_resp = MagicMock(status_code=400, text="Bad Request")

        async def mock_post(url, **kwargs):
            return create_resp

        with patch("platform_services.instagram.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="container creation failed"):
                await instagram_service.publish_video(
                    credential=valid_credential,
                    video_path="/fake/video.mp4",
                    title="Fail Reel",
                    video_url="https://cdn.example.com/video.mp4",
                )

    @pytest.mark.asyncio
    async def test_publish_container_failure(self, instagram_service, valid_credential):
        """容器发布失败应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        create_resp = MagicMock(status_code=200)
        create_resp.json.return_value = {"id": "container_pub_fail"}

        poll_resp = MagicMock(status_code=200)
        poll_resp.json.return_value = {"status_code": "FINISHED"}

        publish_resp = MagicMock(status_code=500, text="Server Error")

        async def mock_post(url, **kwargs):
            if "/media_publish" in url:
                return publish_resp
            return create_resp

        async def mock_get(url, **kwargs):
            return poll_resp

        with patch("platform_services.instagram.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="publish failed"):
                await instagram_service.publish_video(
                    credential=valid_credential,
                    video_path="/fake/video.mp4",
                    title="Pub Fail",
                    video_url="https://cdn.example.com/video.mp4",
                )

    @pytest.mark.asyncio
    async def test_missing_ig_user_id(self, instagram_service):
        """缺少 ig_user_id 时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        cred = OAuthCredential(
            access_token="token",
            refresh_token="rt",
            expires_at=int(time.time()) + 3600,
            raw="{}",
        )

        with pytest.raises(PublishError, match="missing ig_user_id"):
            await instagram_service.publish_video(
                credential=cred,
                video_path="/fake/video.mp4",
                title="No IG ID",
                video_url="https://cdn.example.com/video.mp4",
            )

    @pytest.mark.asyncio
    async def test_missing_video_url(self, instagram_service, valid_credential):
        """缺少 video_url 时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        with pytest.raises(PublishError, match="missing video_url"):
            await instagram_service.publish_video(
                credential=valid_credential,
                video_path="/fake/video.mp4",
                title="No URL",
            )

    @pytest.mark.asyncio
    async def test_container_creation_api_error(self, instagram_service, valid_credential):
        """容器创建返回 API error 时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        create_resp = MagicMock(status_code=200)
        create_resp.json.return_value = {
            "error": {"message": "Invalid video URL"}
        }

        async def mock_post(url, **kwargs):
            return create_resp

        with patch("platform_services.instagram.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="container error"):
                await instagram_service.publish_video(
                    credential=valid_credential,
                    video_path="/fake/video.mp4",
                    title="API Error",
                    video_url="https://cdn.example.com/bad.mp4",
                )

    @pytest.mark.asyncio
    async def test_ig_user_id_from_platform_options(self, instagram_service):
        """通过 platform_options 传入 ig_user_id。"""
        cred = OAuthCredential(
            access_token="token",
            refresh_token="rt",
            expires_at=int(time.time()) + 3600,
            raw="{}",
        )

        create_resp = MagicMock(status_code=200)
        create_resp.json.return_value = {"id": "container_opt"}

        poll_resp = MagicMock(status_code=200)
        poll_resp.json.return_value = {"status_code": "FINISHED"}

        publish_resp = MagicMock(status_code=200)
        publish_resp.json.return_value = {"id": "post_opt"}

        post_urls = []

        async def mock_post(url, **kwargs):
            post_urls.append(url)
            if "/media_publish" in url:
                return publish_resp
            return create_resp

        async def mock_get(url, **kwargs):
            return poll_resp

        with patch("platform_services.instagram.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await instagram_service.publish_video(
                credential=cred,
                video_path="/fake/video.mp4",
                title="Opt Reel",
                video_url="https://cdn.example.com/video.mp4",
                ig_user_id="custom_ig_999",
            )

        assert result.success is True
        assert any("custom_ig_999" in url for url in post_urls)
