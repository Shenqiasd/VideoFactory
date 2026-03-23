"""
TikTok 平台服务实现。

使用 TikTok OAuth2 进行认证，Content Posting API 发布视频。
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

AUTH_URI = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URI = "https://open.tiktokapis.com/v2/oauth/token/"
USER_INFO_URI = "https://open.tiktokapis.com/v2/user/info/"
PUBLISH_INIT_URI = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"

SCOPES = "user.info.basic,video.publish,video.upload"

DEFAULT_CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB


class TikTokService(PlatformService):
    """TikTok 平台服务（TikTok OAuth2 + Content Posting API）。"""

    platform = PlatformType.TIKTOK
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
        """生成 TikTok OAuth2 授权 URL。"""
        params = {
            "client_key": self.client_id,
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
                    "client_key": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.redirect_uri,
                },
            )
            if token_resp.status_code != 200:
                raise OAuthError(
                    f"TikTok token exchange failed: {token_resp.status_code} {token_resp.text}"
                )
            token_data = token_resp.json()

            if "error" in token_data and token_data["error"]:
                raise OAuthError(
                    f"TikTok token exchange error: {token_data.get('error_description', token_data['error'])}"
                )

            access_token = token_data["access_token"]
            refresh_token = token_data.get("refresh_token", "")
            expires_in = token_data.get("expires_in", 86400)
            expires_at = int(time.time()) + expires_in
            open_id = token_data.get("open_id", "")

            # 2. 获取用户信息
            user_resp = await client.get(
                USER_INFO_URI,
                params={"fields": "open_id,union_id,avatar_url,display_name"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if user_resp.status_code != 200:
                raise OAuthError(
                    f"TikTok user info fetch failed: {user_resp.status_code} {user_resp.text}"
                )
            user_resp_data = user_resp.json()
            user_data = user_resp_data.get("data", {}).get("user", {})

            if user_resp_data.get("error", {}).get("code", "ok") != "ok":
                raise OAuthError(
                    f"TikTok user info error: {user_resp_data['error'].get('message', 'unknown')}"
                )

        credential = OAuthCredential(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            raw=json.dumps(token_data),
        )
        account = PlatformAccount(
            platform=PlatformType.TIKTOK,
            platform_uid=open_id or user_data.get("open_id", ""),
            username=user_data.get("display_name", open_id),
            nickname=user_data.get("display_name", ""),
            avatar_url=user_data.get("avatar_url", ""),
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
                    "client_key": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": credential.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if resp.status_code != 200:
                raise OAuthError(
                    f"TikTok token refresh failed: {resp.status_code} {resp.text}"
                )
            data = resp.json()

            if "error" in data and data["error"]:
                raise OAuthError(
                    f"TikTok token refresh error: {data.get('error_description', data['error'])}"
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
        上传视频到 TikTok（Content Posting API）。

        流程: init upload → upload video chunks → publish (async)

        platform_options 支持:
        - privacy_level: str (默认 "SELF_ONLY")
        - disable_comment: bool (默认 False)
        - disable_duet: bool (默认 False)
        - disable_stitch: bool (默认 False)
        """
        privacy_level = platform_options.get("privacy_level", "SELF_ONLY")
        disable_comment = platform_options.get("disable_comment", False)
        disable_duet = platform_options.get("disable_duet", False)
        disable_stitch = platform_options.get("disable_stitch", False)

        file_size = os.path.getsize(video_path)
        auth_headers = {"Authorization": f"Bearer {credential.access_token}"}

        async with httpx.AsyncClient(timeout=120) as client:
            # 1. Initialize upload
            init_body = {
                "post_info": {
                    "title": title,
                    "description": description or title,
                    "privacy_level": privacy_level,
                    "disable_comment": disable_comment,
                    "disable_duet": disable_duet,
                    "disable_stitch": disable_stitch,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": file_size,
                    "chunk_size": DEFAULT_CHUNK_SIZE,
                    "total_chunk_count": (file_size + DEFAULT_CHUNK_SIZE - 1) // DEFAULT_CHUNK_SIZE,
                },
            }

            # 添加话题标签到 description
            if tags:
                tag_text = " ".join(f"#{tag}" for tag in tags)
                init_body["post_info"]["description"] = f"{init_body['post_info']['description']} {tag_text}"

            init_resp = await client.post(
                PUBLISH_INIT_URI,
                json=init_body,
                headers={
                    **auth_headers,
                    "Content-Type": "application/json",
                },
            )
            if init_resp.status_code != 200:
                raise PublishError(
                    f"TikTok upload init failed: {init_resp.status_code} {init_resp.text}"
                )
            init_data = init_resp.json()

            if init_data.get("error", {}).get("code", "ok") != "ok":
                raise PublishError(
                    f"TikTok upload init error: {init_data['error'].get('message', 'unknown')}"
                )

            publish_id = init_data.get("data", {}).get("publish_id", "")
            upload_url = init_data.get("data", {}).get("upload_url", "")

            if not upload_url:
                raise PublishError("TikTok upload init: missing upload_url")

            # 2. Upload video chunks
            chunk_size = DEFAULT_CHUNK_SIZE
            total_chunks = (file_size + chunk_size - 1) // chunk_size

            with open(video_path, "rb") as f:
                for chunk_idx in range(total_chunks):
                    chunk_data = f.read(chunk_size)
                    chunk_start = chunk_idx * chunk_size
                    chunk_end = chunk_start + len(chunk_data) - 1

                    upload_resp = await client.put(
                        upload_url,
                        content=chunk_data,
                        headers={
                            "Content-Type": "video/mp4",
                            "Content-Range": f"bytes {chunk_start}-{chunk_end}/{file_size}",
                        },
                    )
                    if upload_resp.status_code not in (200, 201, 202, 206):
                        raise PublishError(
                            f"TikTok chunk upload failed at chunk {chunk_idx}: "
                            f"{upload_resp.status_code}"
                        )
                    logger.info(
                        "TikTok 上传进度: chunk %d/%d", chunk_idx + 1, total_chunks
                    )

        logger.info("TikTok 视频上传完成，异步发布中: publish_id=%s", publish_id)
        return PublishResult(
            success=True,
            post_id=publish_id,
            permalink="",  # TikTok 不在上传时返回 permalink
            status="publishing",
        )
