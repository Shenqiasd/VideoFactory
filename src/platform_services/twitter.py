"""
Twitter/X 平台服务实现。

使用 Twitter OAuth 2.0 with PKCE 进行认证，chunked media upload 发布视频。
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from typing import List, Optional
from urllib.parse import urlencode

import httpx
from cachetools import TTLCache

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

AUTH_URI = "https://twitter.com/i/oauth2/authorize"
TOKEN_URI = "https://api.twitter.com/2/oauth2/token"
USERINFO_URI = "https://api.twitter.com/2/users/me"
MEDIA_UPLOAD_URI = "https://upload.twitter.com/1.1/media/upload.json"
TWEET_URI = "https://api.twitter.com/2/tweets"

SCOPES = "tweet.read tweet.write users.read offline.access"

DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB

# In-memory store for PKCE code_verifiers keyed by state (TTL=600s to match oauth_states)
_pkce_store: TTLCache = TTLCache(maxsize=10000, ttl=600)


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


class TwitterService(PlatformService):
    """Twitter/X 平台服务（OAuth 2.0 with PKCE + Media Upload API）。"""

    platform = PlatformType.TWITTER
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
        """生成 Twitter OAuth 2.0 授权 URL（with PKCE）。"""
        code_verifier, code_challenge = _generate_pkce()
        # Store code_verifier keyed by state for later retrieval in callback
        _pkce_store[state] = code_verifier

        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{AUTH_URI}?{urlencode(params)}"

    async def handle_callback(
        self, code: str, state: str,
    ) -> tuple[PlatformAccount, OAuthCredential]:
        """用 authorization code + PKCE code_verifier 换取 token 并获取用户信息。"""
        code_verifier = _pkce_store.pop(state, None)
        if not code_verifier:
            raise OAuthError("Twitter PKCE code_verifier not found for state")

        async with httpx.AsyncClient(timeout=30) as client:
            # 1. Exchange code for token
            token_resp = await client.post(
                TOKEN_URI,
                data={
                    "code": code,
                    "grant_type": "authorization_code",
                    "client_id": self.client_id,
                    "redirect_uri": self.redirect_uri,
                    "code_verifier": code_verifier,
                },
                auth=(self.client_id, self.client_secret),
            )
            if token_resp.status_code != 200:
                raise OAuthError(
                    f"Twitter token exchange failed: {token_resp.status_code} {token_resp.text}"
                )
            token_data = token_resp.json()

            access_token = token_data["access_token"]
            refresh_token = token_data.get("refresh_token", "")
            expires_in = token_data.get("expires_in", 7200)
            expires_at = int(time.time()) + expires_in

            # 2. Get user info
            user_resp = await client.get(
                USERINFO_URI,
                params={"user.fields": "profile_image_url"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if user_resp.status_code != 200:
                raise OAuthError(
                    f"Twitter user info fetch failed: {user_resp.status_code} {user_resp.text}"
                )
            user_data = user_resp.json().get("data", {})

        credential = OAuthCredential(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            raw=json.dumps(token_data),
        )
        account = PlatformAccount(
            platform=PlatformType.TWITTER,
            platform_uid=user_data.get("id", ""),
            username=user_data.get("username", ""),
            nickname=user_data.get("name", ""),
            avatar_url=user_data.get("profile_image_url", ""),
        )
        return account, credential

    async def refresh_token(
        self, credential: OAuthCredential,
    ) -> OAuthCredential:
        """刷新 access_token（Twitter 返回新的 refresh_token）。"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                TOKEN_URI,
                data={
                    "refresh_token": credential.refresh_token,
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                },
                auth=(self.client_id, self.client_secret),
            )
            if resp.status_code != 200:
                raise OAuthError(
                    f"Twitter token refresh failed: {resp.status_code} {resp.text}"
                )
            data = resp.json()

        return OAuthCredential(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", credential.refresh_token),
            expires_at=int(time.time()) + data.get("expires_in", 7200),
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
        上传视频到 Twitter（chunked media upload + tweet creation）。

        流程: INIT → APPEND (chunks) → FINALIZE → STATUS check → create tweet
        """
        file_size = os.path.getsize(video_path)
        file_name = os.path.basename(video_path)

        auth_headers = {"Authorization": f"Bearer {credential.access_token}"}

        async with httpx.AsyncClient(timeout=120) as client:
            # 1. INIT - Initialize upload
            init_resp = await client.post(
                MEDIA_UPLOAD_URI,
                data={
                    "command": "INIT",
                    "total_bytes": str(file_size),
                    "media_type": "video/mp4",
                    "media_category": "tweet_video",
                },
                headers=auth_headers,
            )
            if init_resp.status_code != 202:
                raise PublishError(
                    f"Twitter media INIT failed: {init_resp.status_code} {init_resp.text}"
                )
            init_data = init_resp.json()
            media_id = init_data.get("media_id_string", "")
            if not media_id:
                raise PublishError("Twitter media INIT: missing media_id")

            # 2. APPEND - Upload chunks
            chunk_size = DEFAULT_CHUNK_SIZE
            segment_index = 0
            with open(video_path, "rb") as f:
                while True:
                    chunk_data = f.read(chunk_size)
                    if not chunk_data:
                        break
                    append_resp = await client.post(
                        MEDIA_UPLOAD_URI,
                        data={
                            "command": "APPEND",
                            "media_id": media_id,
                            "segment_index": str(segment_index),
                        },
                        files={"media_data": (file_name, chunk_data)},
                        headers=auth_headers,
                    )
                    if append_resp.status_code not in (200, 202, 204):
                        raise PublishError(
                            f"Twitter media APPEND failed at segment {segment_index}: "
                            f"{append_resp.status_code}"
                        )
                    logger.info(
                        "Twitter 上传进度: segment %d", segment_index
                    )
                    segment_index += 1

            # 3. FINALIZE
            finalize_resp = await client.post(
                MEDIA_UPLOAD_URI,
                data={
                    "command": "FINALIZE",
                    "media_id": media_id,
                },
                headers=auth_headers,
            )
            if finalize_resp.status_code not in (200, 201):
                raise PublishError(
                    f"Twitter media FINALIZE failed: {finalize_resp.status_code}"
                )
            finalize_data = finalize_resp.json()

            # 4. Check processing status if needed
            processing_info = finalize_data.get("processing_info")
            if processing_info:
                await self._wait_for_processing(
                    client, media_id, auth_headers, processing_info
                )

            # 5. Create tweet with media
            tweet_text = title
            if description:
                tweet_text = f"{title}\n\n{description}"
            if tags:
                hashtags = " ".join(f"#{tag}" for tag in tags)
                tweet_text = f"{tweet_text}\n{hashtags}"

            tweet_resp = await client.post(
                TWEET_URI,
                json={
                    "text": tweet_text,
                    "media": {"media_ids": [media_id]},
                },
                headers={
                    **auth_headers,
                    "Content-Type": "application/json",
                },
            )
            if tweet_resp.status_code not in (200, 201):
                raise PublishError(
                    f"Twitter tweet creation failed: {tweet_resp.status_code} {tweet_resp.text}"
                )
            tweet_data = tweet_resp.json().get("data", {})
            tweet_id = tweet_data.get("id", "")

        logger.info("Twitter 视频发布成功: tweet_id=%s", tweet_id)
        return PublishResult(
            success=True,
            post_id=tweet_id,
            permalink=f"https://twitter.com/i/status/{tweet_id}" if tweet_id else "",
            status="published",
        )

    async def _wait_for_processing(
        self,
        client: httpx.AsyncClient,
        media_id: str,
        headers: dict,
        processing_info: dict,
    ) -> None:
        """Poll media processing status until complete."""
        import asyncio

        max_retries = 30
        for _ in range(max_retries):
            state = processing_info.get("state", "")
            if state == "succeeded":
                return
            if state == "failed":
                error = processing_info.get("error", {})
                raise PublishError(
                    f"Twitter media processing failed: {error.get('message', 'unknown')}"
                )

            wait_secs = processing_info.get("check_after_secs", 5)
            await asyncio.sleep(wait_secs)

            status_resp = await client.get(
                MEDIA_UPLOAD_URI,
                params={
                    "command": "STATUS",
                    "media_id": media_id,
                },
                headers=headers,
            )
            if status_resp.status_code != 200:
                raise PublishError(
                    f"Twitter media STATUS check failed: {status_resp.status_code}"
                )
            processing_info = status_resp.json().get("processing_info", {})

        # Final check for the last poll result
        state = processing_info.get("state", "")
        if state == "succeeded":
            return
        if state == "failed":
            error = processing_info.get("error", {})
            raise PublishError(
                f"Twitter media processing failed: {error.get('message', 'unknown')}"
            )
        raise PublishError("Twitter media processing timed out")
