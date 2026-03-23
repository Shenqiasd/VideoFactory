"""
快手（Kwai/Kuaishou）平台服务实现。

使用快手开放平台 OAuth2 进行认证，视频接口发布视频。
文档: https://open.kuaishou.com/platform/openApi
"""

import json
import logging
import os
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

AUTH_URI = "https://open.kuaishou.com/oauth2/authorize"
TOKEN_URI = "https://open.kuaishou.com/oauth2/access_token"
REFRESH_URI = "https://open.kuaishou.com/oauth2/refresh_token"
USER_INFO_URI = "https://open.kuaishou.com/openapi/user_info"
UPLOAD_URI = "https://open.kuaishou.com/openapi/photo/upload"
PUBLISH_URI = "https://open.kuaishou.com/openapi/photo/publish"

SCOPES = "user_info,video_publish"


class KwaiService(PlatformService):
    """快手平台服务（Kuaishou Open Platform OAuth2）。"""

    platform = PlatformType.KWAI
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
        """生成快手 OAuth2 授权 URL。"""
        params = {
            "app_id": self.client_id,
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
            # 1. 用 code 换 token
            token_resp = await client.post(
                TOKEN_URI,
                data={
                    "app_id": self.client_id,
                    "app_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
            )
            if token_resp.status_code != 200:
                raise OAuthError(
                    f"Kwai token exchange failed: {token_resp.status_code} {token_resp.text}"
                )
            resp_data = token_resp.json()

            if resp_data.get("result") != 1:
                raise OAuthError(
                    f"Kwai token exchange error: {resp_data.get('error_msg', 'unknown')}"
                )

            access_token = resp_data["access_token"]
            refresh_token = resp_data.get("refresh_token", "")
            expires_in = resp_data.get("expires_in", 86400)
            expires_at = int(time.time()) + expires_in
            open_id = resp_data.get("open_id", "")

            # 2. 获取用户信息
            user_resp = await client.get(
                USER_INFO_URI,
                params={"app_id": self.client_id, "access_token": access_token},
            )
            if user_resp.status_code != 200:
                raise OAuthError(
                    f"Kwai user info fetch failed: {user_resp.status_code} {user_resp.text}"
                )
            user_data = user_resp.json()

            if user_data.get("result") != 1:
                raise OAuthError(
                    f"Kwai user info error: {user_data.get('error_msg', 'unknown')}"
                )

            user_info = user_data.get("user_info", {})

        credential = OAuthCredential(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            raw=json.dumps(resp_data),
        )
        account = PlatformAccount(
            platform=PlatformType.KWAI,
            platform_uid=open_id,
            username=user_info.get("name", open_id),
            nickname=user_info.get("name", ""),
            avatar_url=user_info.get("head", ""),
        )
        return account, credential

    async def refresh_token(
        self, credential: OAuthCredential,
    ) -> OAuthCredential:
        """刷新 access_token。"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                REFRESH_URI,
                data={
                    "app_id": self.client_id,
                    "app_secret": self.client_secret,
                    "refresh_token": credential.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if resp.status_code != 200:
                raise OAuthError(
                    f"Kwai token refresh failed: {resp.status_code} {resp.text}"
                )
            data = resp.json()

            if data.get("result") != 1:
                raise OAuthError(
                    f"Kwai token refresh error: {data.get('error_msg', 'unknown')}"
                )

        return OAuthCredential(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", credential.refresh_token),
            expires_at=int(time.time()) + data.get("expires_in", 86400),
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
        上传视频到快手。

        流程: upload video → publish
        """
        auth_params = {
            "app_id": self.client_id,
            "access_token": credential.access_token,
        }

        async with httpx.AsyncClient(timeout=120) as client:
            # 1. 上传视频
            with open(video_path, "rb") as f:
                upload_resp = await client.post(
                    UPLOAD_URI,
                    params=auth_params,
                    files={"file": (os.path.basename(video_path), f, "video/mp4")},
                )
            if upload_resp.status_code != 200:
                raise PublishError(
                    f"Kwai video upload failed: {upload_resp.status_code} {upload_resp.text}"
                )
            upload_data = upload_resp.json()

            if upload_data.get("result") != 1:
                raise PublishError(
                    f"Kwai video upload error: {upload_data.get('error_msg', 'unknown')}"
                )

            upload_token = upload_data.get("upload_token", "")
            if not upload_token:
                raise PublishError("Kwai video upload: missing upload_token")

            # 2. 发布视频
            caption = title
            if description:
                caption = f"{title}\n{description}"
            if tags:
                tag_text = " ".join(f"#{tag}" for tag in tags)
                caption = f"{caption} {tag_text}"

            publish_body = {
                "upload_token": upload_token,
                "caption": caption,
            }
            if cover_path and os.path.exists(cover_path):
                with open(cover_path, "rb") as cf:
                    publish_resp = await client.post(
                        PUBLISH_URI,
                        params=auth_params,
                        data=publish_body,
                        files={"cover": (os.path.basename(cover_path), cf, "image/jpeg")},
                    )
            else:
                publish_resp = await client.post(
                    PUBLISH_URI,
                    params=auth_params,
                    data=publish_body,
                )

            if publish_resp.status_code != 200:
                raise PublishError(
                    f"Kwai publish failed: {publish_resp.status_code} {publish_resp.text}"
                )
            publish_data = publish_resp.json()

            if publish_data.get("result") != 1:
                raise PublishError(
                    f"Kwai publish error: {publish_data.get('error_msg', 'unknown')}"
                )

            photo_id = publish_data.get("photo_id", "")

        logger.info("快手视频发布成功: photo_id=%s", photo_id)
        return PublishResult(
            success=True,
            post_id=photo_id,
            permalink=f"https://www.kuaishou.com/short-video/{photo_id}" if photo_id else "",
            status="published",
        )
