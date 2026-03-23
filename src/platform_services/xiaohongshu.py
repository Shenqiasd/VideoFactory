"""
小红书（Xiaohongshu / RED）平台服务实现。

使用小红书开放平台 OAuth2 进行认证，内容发布接口发布视频。
文档: https://open.xiaohongshu.com/document
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

AUTH_URI = "https://open.xiaohongshu.com/oauth/authorize"
TOKEN_URI = "https://open.xiaohongshu.com/oauth/token"
REFRESH_URI = "https://open.xiaohongshu.com/oauth/token"
USER_INFO_URI = "https://open.xiaohongshu.com/api/user/info"
PUBLISH_URI = "https://open.xiaohongshu.com/api/media/video/publish"

SCOPES = "user_info,content_publish"


class XiaohongshuService(PlatformService):
    """小红书平台服务（Xiaohongshu Open Platform OAuth2）。"""

    platform = PlatformType.XIAOHONGSHU
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
        """生成小红书 OAuth2 授权 URL。"""
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
                json={
                    "app_id": self.client_id,
                    "app_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
            )
            if token_resp.status_code != 200:
                raise OAuthError(
                    f"Xiaohongshu token exchange failed: {token_resp.status_code} {token_resp.text}"
                )
            resp_data = token_resp.json()

            if resp_data.get("code") != 0:
                raise OAuthError(
                    f"Xiaohongshu token exchange error: {resp_data.get('msg', 'unknown')}"
                )

            token_data = resp_data.get("data", {})
            access_token = token_data["access_token"]
            refresh_token = token_data.get("refresh_token", "")
            expires_in = token_data.get("expires_in", 86400)
            expires_at = int(time.time()) + expires_in

            # 2. 获取用户信息
            user_resp = await client.get(
                USER_INFO_URI,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if user_resp.status_code != 200:
                raise OAuthError(
                    f"Xiaohongshu user info fetch failed: {user_resp.status_code} {user_resp.text}"
                )
            user_resp_data = user_resp.json()

            if user_resp_data.get("code") != 0:
                raise OAuthError(
                    f"Xiaohongshu user info error: {user_resp_data.get('msg', 'unknown')}"
                )

            user_data = user_resp_data.get("data", {})

        credential = OAuthCredential(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            raw=json.dumps(resp_data),
        )
        account = PlatformAccount(
            platform=PlatformType.XIAOHONGSHU,
            platform_uid=user_data.get("user_id", ""),
            username=user_data.get("nickname", ""),
            nickname=user_data.get("nickname", ""),
            avatar_url=user_data.get("avatar", ""),
        )
        return account, credential

    async def refresh_token(
        self, credential: OAuthCredential,
    ) -> OAuthCredential:
        """刷新 access_token。"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                REFRESH_URI,
                json={
                    "app_id": self.client_id,
                    "app_secret": self.client_secret,
                    "refresh_token": credential.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if resp.status_code != 200:
                raise OAuthError(
                    f"Xiaohongshu token refresh failed: {resp.status_code} {resp.text}"
                )
            resp_data = resp.json()

            if resp_data.get("code") != 0:
                raise OAuthError(
                    f"Xiaohongshu token refresh error: {resp_data.get('msg', 'unknown')}"
                )

            data = resp_data.get("data", {})

        return OAuthCredential(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", credential.refresh_token),
            expires_at=int(time.time()) + data.get("expires_in", 86400),
            raw=json.dumps(resp_data),
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
        上传视频到小红书。

        platform_options 支持:
        - topic_id: str (话题 ID)
        """
        auth_headers = {"Authorization": f"Bearer {credential.access_token}"}

        async with httpx.AsyncClient(timeout=120) as client:
            files = {
                "video": (os.path.basename(video_path), open(video_path, "rb"), "video/mp4"),
            }
            data = {
                "title": title,
                "description": description or title,
            }
            if tags:
                data["tags"] = json.dumps(tags)

            topic_id = platform_options.get("topic_id")
            if topic_id:
                data["topic_id"] = topic_id

            if cover_path and os.path.exists(cover_path):
                files["cover"] = (os.path.basename(cover_path), open(cover_path, "rb"), "image/jpeg")

            try:
                resp = await client.post(
                    PUBLISH_URI,
                    data=data,
                    files=files,
                    headers=auth_headers,
                )
            finally:
                for _, file_tuple in files.items():
                    file_tuple[1].close()

            if resp.status_code != 200:
                raise PublishError(
                    f"Xiaohongshu publish failed: {resp.status_code} {resp.text}"
                )
            resp_data = resp.json()

            if resp_data.get("code") != 0:
                raise PublishError(
                    f"Xiaohongshu publish error: {resp_data.get('msg', 'unknown')}"
                )

            note_id = resp_data.get("data", {}).get("note_id", "")

        logger.info("小红书视频发布成功: note_id=%s", note_id)
        return PublishResult(
            success=True,
            post_id=note_id,
            permalink=f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else "",
            status="published",
        )
