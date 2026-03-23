"""
Threads 平台服务实现。

使用 Meta/Threads OAuth2 进行认证，Threads Publishing API 发布视频。
文档: https://developers.facebook.com/docs/threads
"""

import asyncio
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

AUTH_URI = "https://threads.net/oauth/authorize"
TOKEN_URI = "https://graph.threads.net/oauth/access_token"
LONG_LIVED_TOKEN_URI = "https://graph.threads.net/access_token"
GRAPH_API_BASE = "https://graph.threads.net/v1.0"

SCOPES = "threads_basic,threads_content_publish"

# 容器状态轮询
POLL_INTERVAL = 5
POLL_TIMEOUT = 300


class ThreadsService(PlatformService):
    """Threads 平台服务（Meta Threads API）。"""

    platform = PlatformType.THREADS
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
        """生成 Threads OAuth2 授权 URL。"""
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
        """
        处理 OAuth 回调：
        1. 用 code 换取短期 token
        2. 换取长期 token
        3. 获取用户信息
        """
        async with httpx.AsyncClient(timeout=30) as client:
            # 1. code → 短期 token
            token_resp = await client.post(
                TOKEN_URI,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.redirect_uri,
                },
            )
            if token_resp.status_code != 200:
                raise OAuthError(
                    f"Threads token exchange failed: {token_resp.status_code} {token_resp.text}"
                )
            token_data = token_resp.json()

            if "error" in token_data:
                raise OAuthError(
                    f"Threads token exchange error: {token_data['error'].get('message', 'unknown')}"
                )

            short_lived_token = token_data["access_token"]
            user_id = str(token_data.get("user_id", ""))

            # 2. 短期 → 长期 token
            ll_resp = await client.get(
                LONG_LIVED_TOKEN_URI,
                params={
                    "grant_type": "th_exchange_token",
                    "client_secret": self.client_secret,
                    "access_token": short_lived_token,
                },
            )
            if ll_resp.status_code != 200:
                raise OAuthError(
                    f"Threads long-lived token exchange failed: {ll_resp.status_code} {ll_resp.text}"
                )
            ll_data = ll_resp.json()

            if "error" in ll_data:
                raise OAuthError(
                    f"Threads long-lived token error: {ll_data['error'].get('message', 'unknown')}"
                )

            long_lived_token = ll_data["access_token"]
            expires_in = ll_data.get("expires_in", 5184000)  # 默认 60 天

            # 3. 获取用户信息
            me_resp = await client.get(
                f"{GRAPH_API_BASE}/me",
                params={
                    "fields": "id,username,name,threads_profile_picture_url",
                    "access_token": long_lived_token,
                },
            )
            if me_resp.status_code != 200:
                raise OAuthError(
                    f"Threads user info fetch failed: {me_resp.status_code} {me_resp.text}"
                )
            me_data = me_resp.json()

            if "error" in me_data:
                raise OAuthError(
                    f"Threads user info error: {me_data['error'].get('message', 'unknown')}"
                )

        credential = OAuthCredential(
            access_token=long_lived_token,
            refresh_token=long_lived_token,  # Threads 使用 token 交换刷新
            expires_at=int(time.time()) + expires_in,
            raw=json.dumps({"user_id": user_id}),
        )
        account = PlatformAccount(
            platform=PlatformType.THREADS,
            platform_uid=me_data.get("id", user_id),
            username=me_data.get("username", ""),
            nickname=me_data.get("name", me_data.get("username", "")),
            avatar_url=me_data.get("threads_profile_picture_url", ""),
        )
        return account, credential

    async def refresh_token(
        self, credential: OAuthCredential,
    ) -> OAuthCredential:
        """
        刷新 Threads token。

        Threads 使用长期 token 交换来延长有效期。
        """
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                LONG_LIVED_TOKEN_URI,
                params={
                    "grant_type": "th_refresh_token",
                    "access_token": credential.access_token,
                },
            )
            if resp.status_code != 200:
                raise OAuthError(
                    f"Threads token refresh failed: {resp.status_code} {resp.text}"
                )
            data = resp.json()

            if "error" in data:
                raise OAuthError(
                    f"Threads token refresh error: {data['error'].get('message', 'unknown')}"
                )

        new_token = data["access_token"]
        expires_in = data.get("expires_in", 5184000)

        return OAuthCredential(
            access_token=new_token,
            refresh_token=new_token,
            expires_at=int(time.time()) + expires_in,
            raw=credential.raw,
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
        发布视频到 Threads（容器模式，类似 Instagram）。

        platform_options 支持:
        - video_url: str (视频的公开 URL，Threads 需要从 URL 抓取视频)
        - text: str (帖子文案，默认使用 title + description)
        """
        raw_data = json.loads(credential.raw or "{}") if credential.raw else {}
        user_id = platform_options.get("user_id", raw_data.get("user_id", ""))
        video_url = platform_options.get("video_url", "")

        if not user_id:
            raise PublishError("Threads publish: missing user_id")
        if not video_url:
            raise PublishError("Threads publish: missing video_url (Threads requires a public video URL)")

        text = platform_options.get("text", "")
        if not text:
            text = title
            if description:
                text = f"{title}\n\n{description}"
            if tags:
                hashtags = " ".join(f"#{tag}" for tag in tags)
                text = f"{text}\n{hashtags}"

        async with httpx.AsyncClient(timeout=60) as client:
            # 1. 创建 media container
            container_resp = await client.post(
                f"{GRAPH_API_BASE}/{user_id}/threads",
                data={
                    "media_type": "VIDEO",
                    "video_url": video_url,
                    "text": text,
                    "access_token": credential.access_token,
                },
            )
            if container_resp.status_code != 200:
                raise PublishError(
                    f"Threads container creation failed: {container_resp.status_code} {container_resp.text}"
                )
            container_data = container_resp.json()
            if "error" in container_data:
                raise PublishError(
                    f"Threads container error: {container_data['error'].get('message', 'unknown')}"
                )

            container_id = container_data.get("id", "")
            if not container_id:
                raise PublishError("Threads container creation: missing container id")

            # 2. 轮询 container 状态
            start_time = time.time()
            while True:
                elapsed = time.time() - start_time
                if elapsed > POLL_TIMEOUT:
                    raise PublishError(
                        f"Threads container polling timeout after {POLL_TIMEOUT}s"
                    )

                status_resp = await client.get(
                    f"{GRAPH_API_BASE}/{container_id}",
                    params={
                        "fields": "status",
                        "access_token": credential.access_token,
                    },
                )
                if status_resp.status_code == 200:
                    status_data = status_resp.json()
                    status = status_data.get("status", "")

                    if status == "FINISHED":
                        break
                    if status == "ERROR":
                        raise PublishError(
                            f"Threads container processing failed (container_id={container_id})"
                        )

                await asyncio.sleep(POLL_INTERVAL)

            # 3. 发布 container
            publish_resp = await client.post(
                f"{GRAPH_API_BASE}/{user_id}/threads_publish",
                data={
                    "creation_id": container_id,
                    "access_token": credential.access_token,
                },
            )
            if publish_resp.status_code != 200:
                raise PublishError(
                    f"Threads publish failed: {publish_resp.status_code} {publish_resp.text}"
                )
            publish_data = publish_resp.json()
            if "error" in publish_data:
                raise PublishError(
                    f"Threads publish error: {publish_data['error'].get('message', 'unknown')}"
                )

            post_id = publish_data.get("id", "")

        logger.info("Threads 视频发布成功: post_id=%s", post_id)
        return PublishResult(
            success=True,
            post_id=post_id,
            permalink=f"https://www.threads.net/post/{post_id}" if post_id else "",
            status="published",
        )
