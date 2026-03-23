"""
YouTube 平台服务实现。

使用 Google OAuth2 进行认证，google-api-python-client 进行视频上传。
"""

import json
import logging
import time
from typing import List, Optional
from urllib.parse import urlencode

import httpx
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

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

AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
USERINFO_URI = "https://www.googleapis.com/oauth2/v3/userinfo"
CHANNEL_URI = "https://www.googleapis.com/youtube/v3/channels"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/userinfo.profile",
]


class YouTubeService(PlatformService):
    """YouTube 平台服务（Google OAuth2 + YouTube Data API v3）。"""

    platform = PlatformType.YOUTUBE
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
        """生成 Google OAuth2 授权 URL。"""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{AUTH_URI}?{urlencode(params)}"

    async def handle_callback(
        self, code: str, state: str,
    ) -> tuple[PlatformAccount, OAuthCredential]:
        """用 authorization code 换取 token 并获取频道信息。"""
        async with httpx.AsyncClient(timeout=30) as client:
            # 1. 用 code 换 token
            token_resp = await client.post(
                TOKEN_URI,
                data={
                    "code": code,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": self.redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            if token_resp.status_code != 200:
                raise OAuthError(
                    f"YouTube token exchange failed: {token_resp.status_code} {token_resp.text}"
                )
            token_data = token_resp.json()

            access_token = token_data["access_token"]
            refresh_token = token_data.get("refresh_token", "")
            expires_in = token_data.get("expires_in", 3600)
            expires_at = int(time.time()) + expires_in

            # 2. 获取频道信息
            channel_resp = await client.get(
                CHANNEL_URI,
                params={"part": "snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if channel_resp.status_code != 200:
                raise OAuthError(
                    f"YouTube channel info fetch failed: {channel_resp.status_code} {channel_resp.text}"
                )
            channel_data = channel_resp.json()
            items = channel_data.get("items", [])
            if not items:
                raise OAuthError("YouTube 未找到关联的频道")

            snippet = items[0]["snippet"]
            channel_id = items[0]["id"]

        credential = OAuthCredential(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            raw=json.dumps(token_data),
        )
        account = PlatformAccount(
            platform=PlatformType.YOUTUBE,
            platform_uid=channel_id,
            username=snippet.get("customUrl", channel_id),
            nickname=snippet.get("title", ""),
            avatar_url=snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
        )
        return account, credential

    async def refresh_token(
        self, credential: OAuthCredential,
    ) -> OAuthCredential:
        """刷新 access_token（Google 不返回新 refresh_token，保留原始值）。"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                TOKEN_URI,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": credential.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if resp.status_code != 200:
                raise OAuthError(
                    f"YouTube token refresh failed: {resp.status_code} {resp.text}"
                )
            data = resp.json()

        return OAuthCredential(
            access_token=data["access_token"],
            refresh_token=credential.refresh_token,  # Google 不返回新 refresh_token
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
        上传视频到 YouTube（使用 google-api-python-client 的 resumable upload）。

        platform_options 支持:
        - category_id: str (默认 "22" = People & Blogs)
        - privacy_status: str (默认 "private")
        """
        category_id = platform_options.get("category_id", "22")
        privacy_status = platform_options.get("privacy_status", "private")

        credentials = Credentials(
            token=credential.access_token,
            refresh_token=credential.refresh_token,
            token_uri=TOKEN_URI,
            client_id=self.client_id,
            client_secret=self.client_secret,
        )

        youtube = build("youtube", "v3", credentials=credentials)

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags or [],
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy_status,
            },
        }

        media = MediaFileUpload(
            video_path,
            mimetype="video/*",
            resumable=True,
            chunksize=10 * 1024 * 1024,  # 10 MB chunks
        )

        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        # 执行 resumable upload
        response = None
        try:
            while response is None:
                status, response = request.next_chunk()
                if status:
                    logger.info(
                        "YouTube 上传进度: %.1f%%", status.progress() * 100
                    )
        except Exception as e:
            raise PublishError(f"YouTube 上传失败: {e}")

        video_id = response.get("id", "")
        logger.info("YouTube 视频上传成功: video_id=%s", video_id)

        return PublishResult(
            success=True,
            post_id=video_id,
            permalink=f"https://www.youtube.com/watch?v={video_id}",
            status="published",
        )
