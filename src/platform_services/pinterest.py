"""
Pinterest 平台服务实现。

使用 Pinterest OAuth2 进行认证，Media API + Pin API 发布视频。
"""

import json
import logging
import time
from typing import List, Optional
from urllib.parse import urlencode

import httpx

from .base import (
    AuthMethod,
    OAuthCredential,
    PlatformAccount,
    PlatformService,
    PlatformType,
    PublishResult,
)
from .exceptions import OAuthError, PublishError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AUTH_URI = "https://www.pinterest.com/oauth/"
TOKEN_URI = "https://api.pinterest.com/v5/oauth/token"
USERINFO_URI = "https://api.pinterest.com/v5/user_account"
MEDIA_URI = "https://api.pinterest.com/v5/media"
PINS_URI = "https://api.pinterest.com/v5/pins"

SCOPES = "boards:read,pins:read,pins:write"


class PinterestService(PlatformService):
    """Pinterest 平台服务（Standard OAuth2 + Media/Pin API）。"""

    platform = PlatformType.PINTEREST
    auth_method = AuthMethod.OAUTH2

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------

    async def get_auth_url(self, state: str, **kwargs) -> str:
        """生成 Pinterest OAuth2 授权 URL。"""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
        }
        return f"{AUTH_URI}?{urlencode(params)}"

    async def handle_callback(
        self, code: str, state: str,
    ) -> tuple[PlatformAccount, OAuthCredential]:
        """用 authorization code 换取 token 并获取用户信息。"""
        async with httpx.AsyncClient(timeout=30) as client:
            # 1. Exchange code for token
            token_resp = await client.post(
                TOKEN_URI,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                },
                auth=(self.client_id, self.client_secret),
            )
            if token_resp.status_code != 200:
                raise OAuthError(
                    f"Pinterest token exchange failed: {token_resp.status_code} {token_resp.text}"
                )
            token_data = token_resp.json()

            access_token = token_data["access_token"]
            refresh_token = token_data.get("refresh_token", "")
            expires_in = token_data.get("expires_in", 3600)
            expires_at = int(time.time()) + expires_in

            # 2. Get user info
            user_resp = await client.get(
                USERINFO_URI,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if user_resp.status_code != 200:
                raise OAuthError(
                    f"Pinterest user info fetch failed: {user_resp.status_code} {user_resp.text}"
                )
            user_data = user_resp.json()

        credential = OAuthCredential(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            raw=json.dumps(token_data),
        )
        account = PlatformAccount(
            platform=PlatformType.PINTEREST,
            platform_uid=user_data.get("username", ""),
            username=user_data.get("username", ""),
            nickname=user_data.get("business_name", user_data.get("username", "")),
            avatar_url=user_data.get("profile_image", ""),
        )
        return account, credential

    async def refresh_token(
        self, credential: OAuthCredential,
    ) -> OAuthCredential:
        """刷新 access_token。"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                TOKEN_URI,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": credential.refresh_token,
                },
                auth=(self.client_id, self.client_secret),
            )
            if resp.status_code != 200:
                raise OAuthError(
                    f"Pinterest token refresh failed: {resp.status_code} {resp.text}"
                )
            data = resp.json()

        return OAuthCredential(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", credential.refresh_token),
            expires_at=int(time.time()) + data.get("expires_in", 3600),
            raw=json.dumps(data),
        )

    async def check_token_status(
        self, credential: OAuthCredential,
    ) -> bool:
        """检查 token 是否在 600 秒内仍然有效。"""
        return credential.expires_at - time.time() > 600

    # ------------------------------------------------------------------
    # 发布
    # ------------------------------------------------------------------

    async def publish_video(
        self,
        credential: OAuthCredential,
        video_path: str,
        title: str,
        description: str = "",
        tags: Optional[List[str]] = None,
        cover_path: str = "",
        **platform_options,
    ) -> PublishResult:
        """
        上传视频到 Pinterest（Media API + Pin creation）。

        流程: register media → upload video → check status → create pin

        platform_options 支持:
        - board_id: str (必须，Pinterest Pin 需要指定 board)
        """
        board_id = platform_options.get("board_id", "")

        auth_headers = {"Authorization": f"Bearer {credential.access_token}"}

        async with httpx.AsyncClient(timeout=120) as client:
            # 1. Register media
            register_resp = await client.post(
                MEDIA_URI,
                json={"media_type": "video"},
                headers={
                    **auth_headers,
                    "Content-Type": "application/json",
                },
            )
            if register_resp.status_code not in (200, 201):
                raise PublishError(
                    f"Pinterest media registration failed: {register_resp.status_code} "
                    f"{register_resp.text}"
                )
            register_data = register_resp.json()
            media_id = register_data.get("media_id", "")
            upload_url = register_data.get("upload_url", "")

            if not media_id or not upload_url:
                raise PublishError(
                    "Pinterest media registration: missing media_id or upload_url"
                )

            # 2. Upload video to upload_url
            with open(video_path, "rb") as f:
                video_data = f.read()

            upload_resp = await client.put(
                upload_url,
                content=video_data,
                headers={"Content-Type": "application/octet-stream"},
            )
            if upload_resp.status_code not in (200, 201, 204):
                raise PublishError(
                    f"Pinterest video upload failed: {upload_resp.status_code}"
                )

            # 3. Check media processing status
            await self._wait_for_media(client, media_id, auth_headers)

            # 4. Create pin with video
            pin_body: dict = {
                "title": title,
                "description": description or "",
                "media_source": {
                    "source_type": "video_id",
                    "media_id": media_id,
                },
            }
            if board_id:
                pin_body["board_id"] = board_id

            pin_resp = await client.post(
                PINS_URI,
                json=pin_body,
                headers={
                    **auth_headers,
                    "Content-Type": "application/json",
                },
            )
            if pin_resp.status_code not in (200, 201):
                raise PublishError(
                    f"Pinterest pin creation failed: {pin_resp.status_code} {pin_resp.text}"
                )
            pin_data = pin_resp.json()
            pin_id = pin_data.get("id", "")

        logger.info("Pinterest 视频发布成功: pin_id=%s", pin_id)
        return PublishResult(
            success=True,
            post_id=pin_id,
            permalink=f"https://www.pinterest.com/pin/{pin_id}" if pin_id else "",
            status="published",
        )

    async def _wait_for_media(
        self,
        client: httpx.AsyncClient,
        media_id: str,
        headers: dict,
    ) -> None:
        """Poll media processing status until complete."""
        import asyncio

        max_retries = 30
        for _ in range(max_retries):
            status_resp = await client.get(
                f"{MEDIA_URI}/{media_id}",
                headers=headers,
            )
            if status_resp.status_code != 200:
                raise PublishError(
                    f"Pinterest media status check failed: {status_resp.status_code}"
                )
            status_data = status_resp.json()
            status = status_data.get("status", "")

            if status == "succeeded":
                return
            if status == "failed":
                raise PublishError(
                    f"Pinterest media processing failed: {status_data}"
                )

            await asyncio.sleep(5)

        raise PublishError("Pinterest media processing timed out")
