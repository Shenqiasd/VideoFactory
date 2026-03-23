"""
抖音（Douyin）平台服务实现。

使用抖音开放平台 OAuth2 进行认证，视频接口发布视频。
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

AUTH_URI = "https://open.douyin.com/platform/oauth/connect/"
TOKEN_URI = "https://open.douyin.com/oauth/access_token/"
REFRESH_URI = "https://open.douyin.com/oauth/renew_refresh_token/"
USER_INFO_URI = "https://open.douyin.com/oauth/userinfo/"

UPLOAD_URI = "https://open.douyin.com/api/douyin/v1/video/upload/"
CREATE_URI = "https://open.douyin.com/api/douyin/v1/video/create/"

DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB


class DouyinService(PlatformService):
    """抖音平台服务（Douyin Open Platform OAuth2）。"""

    platform = PlatformType.DOUYIN
    auth_method = AuthMethod.OAUTH2

    def __init__(
        self,
        client_key: str,
        client_secret: str,
        redirect_uri: str,
    ):
        self.client_key = client_key
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------

    async def get_auth_url(self, state: str, **kwargs) -> str:
        """生成抖音 OAuth2 授权 URL。"""
        params = {
            "client_key": self.client_key,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "user_info,video.create,video.data",
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
                    "client_key": self.client_key,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
            )
            if token_resp.status_code != 200:
                raise OAuthError(
                    f"Douyin token exchange failed: {token_resp.status_code} {token_resp.text}"
                )
            resp_data = token_resp.json()

            # 抖音 API 响应格式: {"data": {...}, "extra": {...}}
            token_data = resp_data.get("data", {})
            if token_data.get("error_code", 0) != 0:
                raise OAuthError(
                    f"Douyin token exchange error: {token_data.get('description', 'unknown')}"
                )

            access_token = token_data["access_token"]
            refresh_token = token_data.get("refresh_token", "")
            expires_in = token_data.get("expires_in", 86400)
            expires_at = int(time.time()) + expires_in
            open_id = token_data.get("open_id", "")

            # 2. 获取用户信息
            user_resp = await client.get(
                USER_INFO_URI,
                params={"access_token": access_token, "open_id": open_id},
            )
            if user_resp.status_code != 200:
                raise OAuthError(
                    f"Douyin user info fetch failed: {user_resp.status_code} {user_resp.text}"
                )
            user_resp_data = user_resp.json()
            user_data = user_resp_data.get("data", {})

            if user_data.get("error_code", 0) != 0:
                raise OAuthError(
                    f"Douyin user info error: {user_data.get('description', 'unknown')}"
                )

        credential = OAuthCredential(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            raw=json.dumps(resp_data),
        )
        account = PlatformAccount(
            platform=PlatformType.DOUYIN,
            platform_uid=open_id,
            username=user_data.get("nickname", open_id),
            nickname=user_data.get("nickname", ""),
            avatar_url=user_data.get("avatar", ""),
        )
        return account, credential

    async def refresh_token(
        self, credential: OAuthCredential,
    ) -> OAuthCredential:
        """刷新 access_token（使用 refresh_token 换取新 access_token）。"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                TOKEN_URI,
                data={
                    "client_key": self.client_key,
                    "grant_type": "refresh_token",
                    "refresh_token": credential.refresh_token,
                },
            )
            if resp.status_code != 200:
                raise OAuthError(
                    f"Douyin token refresh failed: {resp.status_code} {resp.text}"
                )
            resp_data = resp.json()
            token_data = resp_data.get("data", {})

            if token_data.get("error_code", 0) != 0:
                raise OAuthError(
                    f"Douyin token refresh error: {token_data.get('description', 'unknown')}"
                )

        return OAuthCredential(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token", credential.refresh_token),
            expires_at=int(time.time()) + token_data.get("expires_in", 86400),
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
        上传视频到抖音。

        流程: upload video → create post

        platform_options 支持:
        - micro_app_id: str (小程序 ID)
        - micro_app_title: str (小程序标题)
        """
        file_size = os.path.getsize(video_path)
        file_name = os.path.basename(video_path)
        open_id = platform_options.get("open_id", "")

        auth_headers = {"Authorization": f"Bearer {credential.access_token}"}

        async with httpx.AsyncClient(timeout=120) as client:
            # 1. 上传视频
            with open(video_path, "rb") as f:
                upload_resp = await client.post(
                    UPLOAD_URI,
                    params={"open_id": open_id},
                    files={"video": (file_name, f, "video/mp4")},
                    headers=auth_headers,
                )
            if upload_resp.status_code != 200:
                raise PublishError(
                    f"Douyin video upload failed: {upload_resp.status_code} {upload_resp.text}"
                )
            upload_data = upload_resp.json()

            # 抖音 API 响应格式: {"data": {...}, "extra": {...}}
            upload_inner = upload_data.get("data", {})
            if upload_inner.get("error_code", 0) != 0:
                raise PublishError(
                    f"Douyin video upload error: {upload_inner.get('description', 'unknown')}"
                )

            video_id = upload_inner.get("video", {}).get("video_id", "")
            if not video_id:
                raise PublishError("Douyin video upload: missing video_id")

            logger.info("Douyin 视频上传成功: video_id=%s", video_id)

            # 2. 创建投稿
            create_body = {
                "video_id": video_id,
                "text": title if not description else f"{title}\n{description}",
            }

            # 添加话题标签
            if tags:
                tag_text = " ".join(f"#{tag}" for tag in tags)
                create_body["text"] = f"{create_body['text']} {tag_text}"

            if cover_path:
                create_body["cover"] = cover_path

            create_resp = await client.post(
                CREATE_URI,
                params={"open_id": open_id},
                json=create_body,
                headers={
                    **auth_headers,
                    "Content-Type": "application/json",
                },
            )
            if create_resp.status_code != 200:
                raise PublishError(
                    f"Douyin create post failed: {create_resp.status_code} {create_resp.text}"
                )
            create_data = create_resp.json()

            create_inner = create_data.get("data", {})
            if create_inner.get("error_code", 0) != 0:
                raise PublishError(
                    f"Douyin create post error: {create_inner.get('description', 'unknown')}"
                )

            item_id = create_inner.get("item_id", "")

        logger.info("Douyin 视频发布成功: item_id=%s", item_id)
        return PublishResult(
            success=True,
            post_id=item_id,
            permalink=f"https://www.douyin.com/video/{item_id}" if item_id else "",
            status="published",
        )
