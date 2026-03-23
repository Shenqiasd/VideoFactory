"""
Sprint 2: BilibiliService 单元测试。

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
from platform_services.bilibili import (  # noqa: E402
    AUTH_URI,
    TOKEN_URI,
    BilibiliService,
)


@pytest.fixture
def bilibili_service():
    return BilibiliService(
        client_id="bili_test_client_id",
        client_secret="bili_test_client_secret",
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
        expires_at=int(time.time()) + 300,  # 5 minutes, within 600s buffer
    )


# ---------------------------------------------------------------------------
# Class attributes
# ---------------------------------------------------------------------------

class TestBilibiliServiceAttributes:
    def test_platform(self, bilibili_service):
        assert bilibili_service.platform == PlatformType.BILIBILI

    def test_auth_method(self, bilibili_service):
        assert bilibili_service.auth_method == AuthMethod.OAUTH2

    def test_client_config(self, bilibili_service):
        assert bilibili_service.client_id == "bili_test_client_id"
        assert bilibili_service.client_secret == "bili_test_client_secret"


# ---------------------------------------------------------------------------
# get_auth_url
# ---------------------------------------------------------------------------

class TestGetAuthUrl:
    @pytest.mark.asyncio
    async def test_generates_correct_url(self, bilibili_service):
        url = await bilibili_service.get_auth_url(state="test_state_abc")
        assert url.startswith(AUTH_URI)
        assert "client_id=bili_test_client_id" in url
        assert "state=test_state_abc" in url
        assert "response_type=code" in url
        assert "redirect_uri=" in url

    @pytest.mark.asyncio
    async def test_includes_redirect_uri(self, bilibili_service):
        url = await bilibili_service.get_auth_url(state="s")
        assert "localhost" in url


# ---------------------------------------------------------------------------
# handle_callback
# ---------------------------------------------------------------------------

class TestHandleCallback:
    @pytest.mark.asyncio
    async def test_successful_callback(self, bilibili_service):
        """测试成功的 OAuth 回调：token 交换 + 用户信息获取。"""
        token_response = {
            "code": 0,
            "data": {
                "access_token": "bili_new_access_token",
                "refresh_token": "bili_new_refresh_token",
                "expires_in": 86400,
            },
        }
        user_response = {
            "code": 0,
            "data": {
                "mid": 12345678,
                "name": "测试用户",
                "face": "https://i0.hdslb.com/bfs/face/test.jpg",
            },
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

        with patch("platform_services.bilibili.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            account, credential = await bilibili_service.handle_callback(
                code="test_code", state="test_state"
            )

        assert account.platform == PlatformType.BILIBILI
        assert account.platform_uid == "12345678"
        assert account.nickname == "测试用户"
        assert account.avatar_url == "https://i0.hdslb.com/bfs/face/test.jpg"
        assert credential.access_token == "bili_new_access_token"
        assert credential.refresh_token == "bili_new_refresh_token"

    @pytest.mark.asyncio
    async def test_token_exchange_http_failure(self, bilibili_service):
        """HTTP 级别失败时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.bilibili.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="token exchange failed"):
                await bilibili_service.handle_callback(code="bad", state="s")

    @pytest.mark.asyncio
    async def test_token_exchange_api_error(self, bilibili_service):
        """Bilibili API 返回 code != 0 时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "code": -101,
            "message": "invalid code",
        }

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.bilibili.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="token exchange error"):
                await bilibili_service.handle_callback(code="bad", state="s")


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------

class TestRefreshToken:
    @pytest.mark.asyncio
    async def test_returns_new_refresh_token(self, bilibili_service, valid_credential):
        """Bilibili 刷新后应返回新的 refresh_token。"""
        refresh_response = {
            "code": 0,
            "data": {
                "access_token": "bili_refreshed_access",
                "refresh_token": "bili_new_refresh_999",
                "expires_in": 86400,
            },
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = refresh_response

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.bilibili.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            new_credential = await bilibili_service.refresh_token(valid_credential)

        assert new_credential.access_token == "bili_refreshed_access"
        assert new_credential.refresh_token == "bili_new_refresh_999"
        assert new_credential.refresh_token != valid_credential.refresh_token
        assert new_credential.expires_at > int(time.time())

    @pytest.mark.asyncio
    async def test_refresh_failure(self, bilibili_service, valid_credential):
        """刷新失败时应抛出 OAuthError。"""
        from platform_services.exceptions import OAuthError

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "code": -101,
            "message": "refresh token expired",
        }

        async def mock_post(url, **kwargs):
            return mock_resp

        with patch("platform_services.bilibili.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(OAuthError, match="refresh error"):
                await bilibili_service.refresh_token(valid_credential)


# ---------------------------------------------------------------------------
# check_token_status
# ---------------------------------------------------------------------------

class TestCheckTokenStatus:
    @pytest.mark.asyncio
    async def test_valid_token(self, bilibili_service, valid_credential):
        """距离过期 > 600s 的 token 应返回 True。"""
        result = await bilibili_service.check_token_status(valid_credential)
        assert result is True

    @pytest.mark.asyncio
    async def test_expiring_token(self, bilibili_service, expiring_credential):
        """距离过期 < 600s 的 token 应返回 False。"""
        result = await bilibili_service.check_token_status(expiring_credential)
        assert result is False

    @pytest.mark.asyncio
    async def test_expired_token(self, bilibili_service):
        """已过期 token 应返回 False。"""
        cred = OAuthCredential(
            access_token="expired",
            refresh_token="rt",
            expires_at=int(time.time()) - 100,
        )
        result = await bilibili_service.check_token_status(cred)
        assert result is False


# ---------------------------------------------------------------------------
# publish_video
# ---------------------------------------------------------------------------

class TestPublishVideo:
    @pytest.mark.asyncio
    async def test_successful_upload(self, bilibili_service, valid_credential, tmp_path):
        """测试完整的分片上传流程: pre-upload → chunk → complete → submit。"""
        # 创建临时视频文件
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 1024)

        preupload_response = {
            "url": "https://upos-sz-upcdnbda2.bilivideo.com/upload",
            "complete": "https://upos-sz-upcdnbda2.bilivideo.com/complete",
            "biz_id": 99999,
            "upos_uri": "upos://ugcfr/test_file.mp4",
            "upload_id": "upload_123",
        }

        chunk_resp = MagicMock()
        chunk_resp.status_code = 200

        complete_resp = MagicMock()
        complete_resp.status_code = 200

        submit_response = {
            "code": 0,
            "data": {
                "bvid": "BV1xx411c7mD",
                "aid": 12345,
            },
        }

        preupload_mock = MagicMock()
        preupload_mock.status_code = 200
        preupload_mock.json.return_value = preupload_response

        submit_mock = MagicMock()
        submit_mock.status_code = 200
        submit_mock.json.return_value = submit_response

        call_count = {"get": 0, "post": 0, "put": 0}

        async def mock_get(url, **kwargs):
            call_count["get"] += 1
            return preupload_mock

        async def mock_put(url, **kwargs):
            call_count["put"] += 1
            return chunk_resp

        async def mock_post(url, **kwargs):
            call_count["post"] += 1
            if "complete" in url:
                return complete_resp
            return submit_mock

        with patch("platform_services.bilibili.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.put = mock_put
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await bilibili_service.publish_video(
                credential=valid_credential,
                video_path=str(video_file),
                title="测试视频",
                description="测试描述",
                tags=["测试", "视频"],
            )

        assert result.success is True
        assert result.post_id == "BV1xx411c7mD"
        assert "bilibili.com/video/BV1xx411c7mD" in result.permalink
        assert call_count["get"] == 1    # pre-upload
        assert call_count["put"] >= 1    # chunk upload(s)
        assert call_count["post"] == 2   # complete + submit

    @pytest.mark.asyncio
    async def test_preupload_failure(self, bilibili_service, valid_credential, tmp_path):
        """pre-upload 失败时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "fail_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        mock_resp = MagicMock()
        mock_resp.status_code = 500

        async def mock_get(url, **kwargs):
            return mock_resp

        with patch("platform_services.bilibili.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="pre-upload failed"):
                await bilibili_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Fail",
                )

    @pytest.mark.asyncio
    async def test_submit_api_error(self, bilibili_service, valid_credential, tmp_path):
        """submit 返回 code != 0 时应抛出 PublishError。"""
        from platform_services.exceptions import PublishError

        video_file = tmp_path / "err_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        preupload_response = {
            "url": "https://upos.bilivideo.com/upload",
            "complete": "https://upos.bilivideo.com/complete",
            "biz_id": 111,
            "upos_uri": "upos://ugcfr/err.mp4",
            "upload_id": "up_err",
        }

        preupload_mock = MagicMock()
        preupload_mock.status_code = 200
        preupload_mock.json.return_value = preupload_response

        ok_resp = MagicMock()
        ok_resp.status_code = 200

        submit_mock = MagicMock()
        submit_mock.status_code = 200
        submit_mock.json.return_value = {
            "code": -4,
            "message": "稿件创建失败",
        }

        async def mock_get(url, **kwargs):
            return preupload_mock

        async def mock_put(url, **kwargs):
            return ok_resp

        async def mock_post(url, **kwargs):
            if "complete" in url:
                return ok_resp
            return submit_mock

        with patch("platform_services.bilibili.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.put = mock_put
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with pytest.raises(PublishError, match="submit error"):
                await bilibili_service.publish_video(
                    credential=valid_credential,
                    video_path=str(video_file),
                    title="Error Video",
                )

    @pytest.mark.asyncio
    async def test_url_scheme_normalization(self, bilibili_service, valid_credential, tmp_path):
        """测试 URL 以 // 开头时自动添加 https: 前缀。"""
        video_file = tmp_path / "scheme_video.mp4"
        video_file.write_bytes(b"\x00" * 512)

        preupload_response = {
            "url": "//upos-sz-upcdnbda2.bilivideo.com/upload",
            "complete": "//upos-sz-upcdnbda2.bilivideo.com/complete",
            "biz_id": 222,
            "upos_uri": "upos://ugcfr/scheme.mp4",
            "upload_id": "up_scheme",
        }

        preupload_mock = MagicMock()
        preupload_mock.status_code = 200
        preupload_mock.json.return_value = preupload_response

        ok_resp = MagicMock()
        ok_resp.status_code = 200

        submit_mock = MagicMock()
        submit_mock.status_code = 200
        submit_mock.json.return_value = {
            "code": 0,
            "data": {"bvid": "BV_scheme", "aid": 333},
        }

        put_urls = []

        async def mock_get(url, **kwargs):
            return preupload_mock

        async def mock_put(url, **kwargs):
            put_urls.append(url)
            return ok_resp

        async def mock_post(url, **kwargs):
            if "complete" in url:
                return ok_resp
            return submit_mock

        with patch("platform_services.bilibili.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.put = mock_put
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await bilibili_service.publish_video(
                credential=valid_credential,
                video_path=str(video_file),
                title="Scheme Video",
            )

        assert result.success is True
        # 验证 put URL 已添加 https: 前缀
        for url in put_urls:
            assert url.startswith("https://")
