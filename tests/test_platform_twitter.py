"""
Sprint 4: TwitterService 单元测试。

覆盖 OAuth 流程（get_auth_url with PKCE, handle_callback, refresh_token, check_token_status）
以及视频发布（chunked media upload + tweet creation）。所有 HTTP 调用均使用 mock。
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
from platform_services.twitter import (  # noqa: E402
    AUTH_URI,
    MEDIA_UPLOAD_URI,
    SCOPES,
    TOKEN_URI,
    TWEET_URI,
    TwitterService,
    _pkce_store,
)


@pytest.fixture
def twitter_service():
    return TwitterService(
        client_id="tw_test_client_id",
        client_secret="tw_test_client_secret",
        redirect_uri="http://localhost:9000/api/oauth/callback/twitter",
    )


@pytest.fixture
def valid_credential():
    return OAuthCredential(
        access_token="tw_access_token_123",
        refresh_token="tw_refresh_token_456",
        expires_at=int(time.time()) + 7200,
    )


@pytest.fixture
def expiring_credential():
    return OAuthCredential(
        access_token="tw_expiring_token",
        refresh_token="tw_refresh_token",
        expires_at=int(time.time()) + 300,  # 5 minutes, within 600s buffer
    )


@pytest.fixture(autouse=True)
def clear_pkce_store():
    """Clear PKCE store before each test."""
    _pkce_store.clear()
    yield
    _pkce_store.clear()


# ---------------------------------------------------------------------------
# Class attributes
# ---------------------------------------------------------------------------

class TestTwitterServiceAttributes:
    def test_platform(self, twitter_service):
        assert twitter_service.platform == PlatformType.TWITTER

    def test_auth_method(self, twitter_service):
        assert twitter_service.auth_method == AuthMethod.OAUTH2

    def test_client_config(self, twitter_service):
        assert twitter_service.client_id == "tw_test_client_id"
        assert twitter_service.client_secret == "tw_test_client_secret"


# ---------------------------------------------------------------------------
# get_auth_url (with PKCE)
# ---------------------------------------------------------------------------

class TestGetAuthUrl:
    @pytest.mark.asyncio
    async def test_generates_correct_url(self, twitter_service):
        url = await twitter_service.get_auth_url(state="test_state_123")
        assert url.startswith(AUTH_URI)
        assert "client_id=tw_test_client_id" in url
        assert "state=test_state_123" in url
        assert "response_type=code" in url

    @pytest.mark.asyncio
    async def test_includes_pkce_code_challenge(self, twitter_service):
        """Auth URL must contain code_challenge and S256 method."""
        url = await twitter_service.get_auth_url(state="pkce_test")
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url

    @pytest.mark.asyncio
    async def test_stores_code_verifier_in_pkce_store(self, twitter_service):
        """PKCE code_verifier should be stored keyed by state."""
        state = "store_test_state"
        await twitter_service.get_auth_url(state=state)
        assert state in _pkce_store
        assert len(_pkce_store[state]) > 40  # code_verifier is a long string

    @pytest.mark.asyncio
    async def test_includes_scopes(self, twitter_service):
        url = await twitter_service.get_auth_url(state="s")
        for scope_part in ["tweet.read", "tweet.write", "users.read", "offline.access"]:
            assert scope_part in url

    @pytest.mark.asyncio
    async def test_includes_redirect_uri(self, twitter_service):
        url = await twitter_service.get_auth_url(state="s")
        assert "redirect_uri=" in url
        assert "localhost" in url


# ---------------------------------------------------------------------------
# handle_callback
# ---------------------------------------------------------------------------

class TestHandleCallback:
    @pytest.mark.asyncio
    async def test_successful_callback(self, twitter_service):
        """测试成功的 OAuth 回调：PKCE token 交换 + 用户信息获取。"""
        state = "callback_state"
        # Pre-populate PKCE store
        _pkce_store[state] = "test_code_verifier_value"

        token_response = {
            "access_token": "tw_new_access_token",
            "refresh_token": "tw_new_refresh_token",
            "expires_in": 7200,
            "token_type": "bearer",
        }
        user_response = {
            "data": {
                "id": "123456789",
                "username": "testuser",
                "name": "Test User",
                "profile_image_url": "https://pbs.twimg.com/profile/test.jpg",
            }
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

        with patch("platform_services.twitter.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            account, credential = await twitter_service.handle_callback(
                code="test_code", state=state
            )

        assert account.platform == PlatformType.TWITTER
        assert account.platform_uid == "123456789"
        assert account.username == "testuser"
        assert account.nickname == "Test User"
        assert account.avatar_url == "https://pbs.twimg.com/profile/test.jpg"
        assert credential.access_token == "tw_new_access_token"
        assert credential.refresh_token == "tw_new_refresh_token"

    @pytest.mark.asyncio
    async def test_missing_code_verifier(self, twitter_service):
        """No PKCE code_verifier for state should raise OAuthError."""
        from platform_services.exceptions import OAuthError

        with pytest.raises(OAuthError, match="code_verifier not found"):
            await twitter_service.handle_callback(
                code="test_code", state="no_such_state"
            )

    @pytest.mark.asyncio
    async def test_token_exchange_failure(self, twitter_service):
        """token 交换失败时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        state = "fail_state"
        _pkce_store[state] = "verifier"

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "invalid_grant"

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.twitter.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="token exchange failed"):
                await twitter_service.handle_callback(code="bad", state=state)

    @pytest.mark.asyncio
    async def test_user_info_fetch_failure(self, twitter_service):
        """用户信息获取失败时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        state = "user_fail_state"
        _pkce_store[state] = "verifier"

        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 7200,
        }

        mock_user_resp = MagicMock()
        mock_user_resp.status_code = 401
        mock_user_resp.text = "Unauthorized"

        async def mock_post(url, **kwargs):
            return mock_token_resp

        async def mock_get(url, **kwargs):
            return mock_user_resp

        with patch("platform_services.twitter.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="user info fetch failed"):
                await twitter_service.handle_callback(code="c", state=state)


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------

class TestRefreshToken:
    @pytest.mark.asyncio
    async def test_returns_new_tokens(self, twitter_service, valid_credential):
        """Twitter 刷新后应返回新的 access_token 和 refresh_token。"""
        refresh_response = {
            "access_token": "tw_refreshed_access",
            "refresh_token": "tw_new_refresh_999",
            "expires_in": 7200,
            "token_type": "bearer",
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = refresh_response

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.twitter.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            new_credential = await twitter_service.refresh_token(valid_credential)

        assert new_credential.access_token == "tw_refreshed_access"
        assert new_credential.refresh_token == "tw_new_refresh_999"
        assert new_credential.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_refresh_failure(self, twitter_service, valid_credential):
        """刷新失败时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "invalid_grant"

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.twitter.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="refresh failed"):
                await twitter_service.refresh_token(valid_credential)


# ---------------------------------------------------------------------------
# check_token_status
# ---------------------------------------------------------------------------

class TestCheckTokenStatus:
    @pytest.mark.asyncio
    async def test_valid_token(self, twitter_service, valid_credential):
        """距离过期 > 600s 的 token 应返回 True。"""
        result = await twitter_service.check_token_status(valid_credential)
        assert result is True

    @pytest.mark.asyncio
    async def test_expiring_token(self, twitter_service, expiring_credential):
        """距离过期 < 600s 的 token 应返回 False。"""
        result = await twitter_service.check_token_status(expiring_credential)
        assert result is False

    @pytest.mark.asyncio
    async def test_expired_token(self, twitter_service):
        """已过期 token 应返回 False。"""
        cred = OAuthCredential(
            access_token="expired",
            refresh_token="rt",
            expires_at=int(time.time()) - 100,
        )
        result = await twitter_service.check_token_status(cred)
        assert result is False


# ---------------------------------------------------------------------------
# publish_video
# ---------------------------------------------------------------------------

class TestPublishVideo:
    @pytest.mark.asyncio
    async def test_successful_upload(self, twitter_service, valid_credential, tmp_path):
        """测试完整的 chunked upload 流程: INIT → APPEND → FINALIZE → tweet。"""
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 1024)

        init_response = {
            "media_id_string": "media_123456",
            "media_id": 123456,
        }
        finalize_response = {
            "media_id_string": "media_123456",
            "processing_info": None,
        }
        tweet_response = {
            "data": {
                "id": "tweet_789",
                "text": "Test Video",
            }
        }

        call_log = []

        async def mock_post(url, **kwargs):
            call_log.append(url)
            resp = MagicMock()

            data = kwargs.get("data", {})
            command = data.get("command", "") if isinstance(data, dict) else ""

            if command == "INIT":
                resp.status_code = 202
                resp.json.return_value = init_response
            elif command == "APPEND":
                resp.status_code = 204
                resp.json.return_value = {}
            elif command == "FINALIZE":
                resp.status_code = 200
                resp.json.return_value = finalize_response
            elif TWEET_URI in url:
                resp.status_code = 201
                resp.json.return_value = tweet_response
            else:
                resp.status_code = 200
                resp.json.return_value = {}

            return resp

        with patch("platform_services.twitter.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await twitter_service.publish_video(
                credential=valid_credential,
                video_path=str(video_file),
                title="Test Video",
                description="Test description",
                tags=["test", "video"],
            )

        assert result.success is True
        assert result.post_id == "tweet_789"
        assert "twitter.com" in result.permalink
        # Verify all steps were called
        assert len(call_log) >= 4  # INIT + APPEND(s) + FINALIZE + tweet

    @pytest.mark.asyncio
    async def test_init_failure(self, twitter_service, valid_credential, tmp_path):
        """INIT 失败时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "fail_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 400
            resp.text = "Bad Request"
            resp.json.return_value = {}
            return resp

        with patch("platform_services.twitter.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="INIT failed"):
                await twitter_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Fail Video",
                )

    @pytest.mark.asyncio
    async def test_append_failure(self, twitter_service, valid_credential, tmp_path):
        """APPEND 失败时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "append_fail.mp4"
        video_file.write_bytes(b"\x00" * 512)

        init_response = {
            "media_id_string": "media_999",
            "media_id": 999,
        }

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            data = kwargs.get("data", {})
            command = data.get("command", "") if isinstance(data, dict) else ""

            if command == "INIT":
                resp.status_code = 202
                resp.json.return_value = init_response
            elif command == "APPEND":
                resp.status_code = 500
                resp.json.return_value = {}
            else:
                resp.status_code = 200
                resp.json.return_value = {}
            return resp

        with patch("platform_services.twitter.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="APPEND failed"):
                await twitter_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Append Fail",
                )

    @pytest.mark.asyncio
    async def test_finalize_failure(self, twitter_service, valid_credential, tmp_path):
        """FINALIZE 失败时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "finalize_fail.mp4"
        video_file.write_bytes(b"\x00" * 512)

        init_response = {"media_id_string": "media_111", "media_id": 111}

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            data = kwargs.get("data", {})
            command = data.get("command", "") if isinstance(data, dict) else ""

            if command == "INIT":
                resp.status_code = 202
                resp.json.return_value = init_response
            elif command == "APPEND":
                resp.status_code = 204
                resp.json.return_value = {}
            elif command == "FINALIZE":
                resp.status_code = 400
                resp.json.return_value = {}
            else:
                resp.status_code = 200
                resp.json.return_value = {}
            return resp

        with patch("platform_services.twitter.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="FINALIZE failed"):
                await twitter_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Finalize Fail",
                )

    @pytest.mark.asyncio
    async def test_tweet_creation_failure(self, twitter_service, valid_credential, tmp_path):
        """Tweet 创建失败时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "tweet_fail.mp4"
        video_file.write_bytes(b"\x00" * 512)

        init_response = {"media_id_string": "media_222", "media_id": 222}
        finalize_response = {"media_id_string": "media_222", "processing_info": None}

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            data = kwargs.get("data", {})
            command = data.get("command", "") if isinstance(data, dict) else ""

            if command == "INIT":
                resp.status_code = 202
                resp.json.return_value = init_response
            elif command == "APPEND":
                resp.status_code = 204
                resp.json.return_value = {}
            elif command == "FINALIZE":
                resp.status_code = 200
                resp.json.return_value = finalize_response
            else:
                # Tweet creation
                resp.status_code = 403
                resp.text = "Forbidden"
                resp.json.return_value = {}
            return resp

        with patch("platform_services.twitter.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="tweet creation failed"):
                await twitter_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Tweet Fail",
                )

    @pytest.mark.asyncio
    async def test_processing_info_succeeded(self, twitter_service, valid_credential, tmp_path):
        """FINALIZE returns processing_info with state=succeeded → proceed to tweet."""
        video_file = tmp_path / "proc_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        init_response = {"media_id_string": "media_333", "media_id": 333}
        finalize_response = {
            "media_id_string": "media_333",
            "processing_info": {"state": "succeeded"},
        }
        tweet_response = {"data": {"id": "tweet_proc_ok"}}

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            data = kwargs.get("data", {})
            command = data.get("command", "") if isinstance(data, dict) else ""

            if command == "INIT":
                resp.status_code = 202
                resp.json.return_value = init_response
            elif command == "APPEND":
                resp.status_code = 204
                resp.json.return_value = {}
            elif command == "FINALIZE":
                resp.status_code = 200
                resp.json.return_value = finalize_response
            else:
                resp.status_code = 201
                resp.json.return_value = tweet_response
            return resp

        with patch("platform_services.twitter.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await twitter_service.publish_video(
                credential=valid_credential,
                video_path=str(video_file),
                title="Processing OK",
            )

        assert result.success is True
        assert result.post_id == "tweet_proc_ok"

    @pytest.mark.asyncio
    async def test_processing_info_failed(self, twitter_service, valid_credential, tmp_path):
        """FINALIZE returns processing_info with state=failed → raise PublishError."""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "proc_fail.mp4"
        video_file.write_bytes(b"\x00" * 512)

        init_response = {"media_id_string": "media_444", "media_id": 444}
        finalize_response = {
            "media_id_string": "media_444",
            "processing_info": {
                "state": "failed",
                "error": {"message": "InvalidMedia"},
            },
        }

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            data = kwargs.get("data", {})
            command = data.get("command", "") if isinstance(data, dict) else ""

            if command == "INIT":
                resp.status_code = 202
                resp.json.return_value = init_response
            elif command == "APPEND":
                resp.status_code = 204
                resp.json.return_value = {}
            elif command == "FINALIZE":
                resp.status_code = 200
                resp.json.return_value = finalize_response
            else:
                resp.status_code = 200
                resp.json.return_value = {}
            return resp

        with patch("platform_services.twitter.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="processing failed"):
                await twitter_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Processing Fail",
                )

    @pytest.mark.asyncio
    async def test_missing_media_id(self, twitter_service, valid_credential, tmp_path):
        """INIT returns no media_id → raise PublishError."""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "no_media_id.mp4"
        video_file.write_bytes(b"\x00" * 512)

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 202
            resp.json.return_value = {"media_id_string": ""}
            return resp

        with patch("platform_services.twitter.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="missing media_id"):
                await twitter_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="No Media ID",
                )
