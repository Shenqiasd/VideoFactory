"""
Sprint 4: LinkedInService 单元测试。

覆盖 OAuth 流程（get_auth_url, handle_callback, refresh_token, check_token_status）
以及视频发布（register upload + upload binary + UGC post）。所有 HTTP 调用均使用 mock。
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
from platform_services.linkedin import (  # noqa: E402
    AUTH_URI,
    REGISTER_UPLOAD_URI,
    TOKEN_URI,
    UGC_POST_URI,
    USERINFO_URI,
    LinkedInService,
)


@pytest.fixture
def linkedin_service():
    return LinkedInService(
        client_id="li_test_client_id",
        client_secret="li_test_client_secret",
        redirect_uri="http://localhost:9000/api/oauth/callback/linkedin",
    )


@pytest.fixture
def valid_credential():
    return OAuthCredential(
        access_token="li_access_token_123",
        refresh_token="li_refresh_token_456",
        expires_at=int(time.time()) + 3600,
    )


@pytest.fixture
def expiring_credential():
    return OAuthCredential(
        access_token="li_expiring_token",
        refresh_token="li_refresh_token",
        expires_at=int(time.time()) + 300,
    )


# ---------------------------------------------------------------------------
# Class attributes
# ---------------------------------------------------------------------------

class TestLinkedInServiceAttributes:
    def test_platform(self, linkedin_service):
        assert linkedin_service.platform == PlatformType.LINKEDIN

    def test_auth_method(self, linkedin_service):
        assert linkedin_service.auth_method == AuthMethod.OAUTH2

    def test_client_config(self, linkedin_service):
        assert linkedin_service.client_id == "li_test_client_id"
        assert linkedin_service.client_secret == "li_test_client_secret"


# ---------------------------------------------------------------------------
# get_auth_url
# ---------------------------------------------------------------------------

class TestGetAuthUrl:
    @pytest.mark.asyncio
    async def test_generates_correct_url(self, linkedin_service):
        url = await linkedin_service.get_auth_url(state="test_state_abc")
        assert url.startswith(AUTH_URI)
        assert "client_id=li_test_client_id" in url
        assert "state=test_state_abc" in url
        assert "response_type=code" in url

    @pytest.mark.asyncio
    async def test_includes_scopes(self, linkedin_service):
        url = await linkedin_service.get_auth_url(state="s")
        assert "w_member_social" in url
        assert "openid" in url
        assert "profile" in url
        assert "email" in url

    @pytest.mark.asyncio
    async def test_includes_redirect_uri(self, linkedin_service):
        url = await linkedin_service.get_auth_url(state="s")
        assert "redirect_uri=" in url
        assert "localhost" in url


# ---------------------------------------------------------------------------
# handle_callback
# ---------------------------------------------------------------------------

class TestHandleCallback:
    @pytest.mark.asyncio
    async def test_successful_callback(self, linkedin_service):
        """测试成功的 OAuth 回调：token 交换 + 用户信息获取。"""
        token_response = {
            "access_token": "li_new_access_token",
            "refresh_token": "li_new_refresh_token",
            "expires_in": 3600,
        }
        user_response = {
            "sub": "abc123def",
            "name": "Test LinkedIn User",
            "email": "test@linkedin.com",
            "picture": "https://media.licdn.com/test.jpg",
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

        with patch("platform_services.linkedin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            account, credential = await linkedin_service.handle_callback(
                code="test_code", state="test_state"
            )

        assert account.platform == PlatformType.LINKEDIN
        assert account.platform_uid == "abc123def"
        assert account.username == "test@linkedin.com"
        assert account.nickname == "Test LinkedIn User"
        assert account.avatar_url == "https://media.licdn.com/test.jpg"
        assert credential.access_token == "li_new_access_token"
        assert credential.refresh_token == "li_new_refresh_token"

    @pytest.mark.asyncio
    async def test_token_exchange_failure(self, linkedin_service):
        """token 交换失败时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "invalid_grant"

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.linkedin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="token exchange failed"):
                await linkedin_service.handle_callback(code="bad", state="s")

    @pytest.mark.asyncio
    async def test_user_info_fetch_failure(self, linkedin_service):
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

        with patch("platform_services.linkedin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="user info fetch failed"):
                await linkedin_service.handle_callback(code="c", state="s")


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------

class TestRefreshToken:
    @pytest.mark.asyncio
    async def test_returns_new_tokens(self, linkedin_service, valid_credential):
        """刷新后应返回新的 access_token。"""
        refresh_response = {
            "access_token": "li_refreshed_access",
            "refresh_token": "li_new_refresh_999",
            "expires_in": 3600,
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = refresh_response

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.linkedin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            new_credential = await linkedin_service.refresh_token(valid_credential)

        assert new_credential.access_token == "li_refreshed_access"
        assert new_credential.refresh_token == "li_new_refresh_999"
        assert new_credential.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_refresh_failure(self, linkedin_service, valid_credential):
        """刷新失败时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "invalid_grant"

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.linkedin.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="refresh failed"):
                await linkedin_service.refresh_token(valid_credential)


# ---------------------------------------------------------------------------
# check_token_status
# ---------------------------------------------------------------------------

class TestCheckTokenStatus:
    @pytest.mark.asyncio
    async def test_valid_token(self, linkedin_service, valid_credential):
        result = await linkedin_service.check_token_status(valid_credential)
        assert result is True

    @pytest.mark.asyncio
    async def test_expiring_token(self, linkedin_service, expiring_credential):
        result = await linkedin_service.check_token_status(expiring_credential)
        assert result is False

    @pytest.mark.asyncio
    async def test_expired_token(self, linkedin_service):
        cred = OAuthCredential(
            access_token="expired",
            refresh_token="rt",
            expires_at=int(time.time()) - 100,
        )
        result = await linkedin_service.check_token_status(cred)
        assert result is False


# ---------------------------------------------------------------------------
# publish_video
# ---------------------------------------------------------------------------

def _make_mock_client(get_fn=None, post_fn=None, put_fn=None):
    """Helper: build a mock httpx.AsyncClient that works with 'async with'."""
    c = AsyncMock()
    if get_fn:
        c.get = get_fn
    if post_fn:
        c.post = post_fn
    if put_fn:
        c.put = put_fn
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    return c


class TestPublishVideo:
    @pytest.mark.asyncio
    async def test_successful_upload(self, linkedin_service, valid_credential, tmp_path):
        """测试完整流程: get user → registerUpload → upload binary → create UGC post。"""
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 1024)

        user_response = {
            "sub": "person_123",
            "name": "Test User",
        }
        register_response = {
            "value": {
                "uploadMechanism": {
                    "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest": {
                        "uploadUrl": "https://api.linkedin.com/mediaUpload/upload123",
                    }
                },
                "asset": "urn:li:digitalmediaAsset:D1234",
            }
        }
        ugc_response = {
            "id": "urn:li:ugcPost:12345",
        }

        def create_client(**kwargs):
            _post_count = {"n": 0}

            async def mock_get(url, **kwargs):
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = user_response
                return resp

            async def mock_post(url, **kwargs):
                _post_count["n"] += 1
                resp = MagicMock()
                if _post_count["n"] == 1:
                    resp.status_code = 200
                    resp.json.return_value = register_response
                else:
                    resp.status_code = 201
                    resp.json.return_value = ugc_response
                return resp

            async def mock_put(url, **kwargs):
                resp = MagicMock()
                resp.status_code = 201
                return resp

            return _make_mock_client(
                get_fn=mock_get, post_fn=mock_post, put_fn=mock_put,
            )

        with patch("platform_services.linkedin.httpx.AsyncClient") as MockClient:
            MockClient.side_effect = create_client

            result = await linkedin_service.publish_video(
                credential=valid_credential,
                video_path=str(video_file),
                title="Test LinkedIn Video",
                description="Test description",
                tags=["test", "linkedin"],
            )

        assert result.success is True
        assert result.post_id == "urn:li:ugcPost:12345"
        assert "linkedin.com/feed/update/" in result.permalink

    @pytest.mark.asyncio
    async def test_register_upload_failure(self, linkedin_service, valid_credential, tmp_path):
        """registerUpload 失败时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "fail_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        user_response = {"sub": "person_fail"}
        call_count = {"n": 0}

        def create_client(**kwargs):
            call_count["n"] += 1

            if call_count["n"] == 1:
                async def mock_get(url, **kwargs):
                    resp = MagicMock()
                    resp.status_code = 200
                    resp.json.return_value = user_response
                    return resp
                return _make_mock_client(get_fn=mock_get)
            else:
                async def mock_post(url, **kwargs):
                    resp = MagicMock()
                    resp.status_code = 400
                    resp.text = "Bad Request"
                    resp.json.return_value = {}
                    return resp
                return _make_mock_client(post_fn=mock_post)

        with patch("platform_services.linkedin.httpx.AsyncClient") as MockClient:
            MockClient.side_effect = create_client

            with pytest.raises(PublishError, match="register upload failed"):
                await linkedin_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Fail",
                )

    @pytest.mark.asyncio
    async def test_video_upload_failure(self, linkedin_service, valid_credential, tmp_path):
        """视频上传失败时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "upload_fail.mp4"
        video_file.write_bytes(b"\x00" * 512)

        user_response = {"sub": "person_upload"}
        register_response = {
            "value": {
                "uploadMechanism": {
                    "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest": {
                        "uploadUrl": "https://api.linkedin.com/mediaUpload/fail",
                    }
                },
                "asset": "urn:li:digitalmediaAsset:FAIL",
            }
        }
        call_count = {"n": 0}

        def create_client(**kwargs):
            call_count["n"] += 1

            if call_count["n"] == 1:
                async def mock_get(url, **kwargs):
                    resp = MagicMock()
                    resp.status_code = 200
                    resp.json.return_value = user_response
                    return resp
                return _make_mock_client(get_fn=mock_get)
            else:
                async def mock_post(url, **kwargs):
                    resp = MagicMock()
                    resp.status_code = 200
                    resp.json.return_value = register_response
                    return resp

                async def mock_put(url, **kwargs):
                    resp = MagicMock()
                    resp.status_code = 500
                    return resp

                return _make_mock_client(post_fn=mock_post, put_fn=mock_put)

        with patch("platform_services.linkedin.httpx.AsyncClient") as MockClient:
            MockClient.side_effect = create_client

            with pytest.raises(PublishError, match="video upload failed"):
                await linkedin_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Upload Fail",
                )

    @pytest.mark.asyncio
    async def test_ugc_post_creation_failure(self, linkedin_service, valid_credential, tmp_path):
        """UGC Post 创建失败时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "ugc_fail.mp4"
        video_file.write_bytes(b"\x00" * 512)

        user_response = {"sub": "person_ugc"}
        register_response = {
            "value": {
                "uploadMechanism": {
                    "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest": {
                        "uploadUrl": "https://api.linkedin.com/mediaUpload/ok",
                    }
                },
                "asset": "urn:li:digitalmediaAsset:OK",
            }
        }
        call_count = {"n": 0}

        def create_client(**kwargs):
            call_count["n"] += 1

            if call_count["n"] == 1:
                async def mock_get(url, **kwargs):
                    resp = MagicMock()
                    resp.status_code = 200
                    resp.json.return_value = user_response
                    return resp
                return _make_mock_client(get_fn=mock_get)
            else:
                _post_count = {"n": 0}

                async def mock_post(url, **kwargs):
                    _post_count["n"] += 1
                    resp = MagicMock()
                    if _post_count["n"] == 1:
                        resp.status_code = 200
                        resp.json.return_value = register_response
                    else:
                        resp.status_code = 422
                        resp.text = "Unprocessable Entity"
                        resp.json.return_value = {}
                    return resp

                async def mock_put(url, **kwargs):
                    resp = MagicMock()
                    resp.status_code = 201
                    return resp

                return _make_mock_client(post_fn=mock_post, put_fn=mock_put)

        with patch("platform_services.linkedin.httpx.AsyncClient") as MockClient:
            MockClient.side_effect = create_client

            with pytest.raises(PublishError, match="UGC post creation failed"):
                await linkedin_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="UGC Fail",
                )

    @pytest.mark.asyncio
    async def test_get_user_info_failure(self, linkedin_service, valid_credential, tmp_path):
        """publish_video 中获取用户信息失败时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "user_fail.mp4"
        video_file.write_bytes(b"\x00" * 512)

        def create_client(**kwargs):
            async def mock_get(url, **kwargs):
                resp = MagicMock()
                resp.status_code = 401
                return resp
            return _make_mock_client(get_fn=mock_get)

        with patch("platform_services.linkedin.httpx.AsyncClient") as MockClient:
            MockClient.side_effect = create_client

            with pytest.raises(PublishError, match="get user info failed"):
                await linkedin_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="User Info Fail",
                )

    @pytest.mark.asyncio
    async def test_missing_upload_url_or_asset(self, linkedin_service, valid_credential, tmp_path):
        """registerUpload returns no uploadUrl → raise PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "no_upload.mp4"
        video_file.write_bytes(b"\x00" * 512)

        user_response = {"sub": "person_no_url"}
        register_response = {
            "value": {
                "uploadMechanism": {
                    "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest": {
                        "uploadUrl": "",
                    }
                },
                "asset": "",
            }
        }
        call_count = {"n": 0}

        def create_client(**kwargs):
            call_count["n"] += 1

            if call_count["n"] == 1:
                async def mock_get(url, **kwargs):
                    resp = MagicMock()
                    resp.status_code = 200
                    resp.json.return_value = user_response
                    return resp
                return _make_mock_client(get_fn=mock_get)
            else:
                async def mock_post(url, **kwargs):
                    resp = MagicMock()
                    resp.status_code = 200
                    resp.json.return_value = register_response
                    return resp
                return _make_mock_client(post_fn=mock_post)

        with patch("platform_services.linkedin.httpx.AsyncClient") as MockClient:
            MockClient.side_effect = create_client

            with pytest.raises(PublishError, match="missing uploadUrl or asset"):
                await linkedin_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="No Upload URL",
                )
