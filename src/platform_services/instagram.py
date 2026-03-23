"""
Instagram 平台服务实现。

使用 Meta OAuth2 进行认证，Instagram Graph API（容器模式）发布视频。
需要 Instagram Business 或 Creator 账号关联到 Facebook Page。
"""

import asyncio
import json
import logging
import time
from typing import List, Optional

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
from .meta_base import MetaBaseService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GRAPH_API_BASE = "https://graph.facebook.com/v18.0"

SCOPES = "instagram_basic,instagram_content_publish"

# 容器状态轮询
POLL_INTERVAL = 5          # 秒
POLL_TIMEOUT = 300         # 最大等待 5 分钟


class InstagramService(MetaBaseService):
    """Instagram 平台服务（Meta OAuth2 + Instagram Graph API 容器模式）。"""

    platform = PlatformType.INSTAGRAM
    SCOPES = SCOPES

    # ------------------------------------------------------------------
    # OAuth — Instagram 特有逻辑
    # ------------------------------------------------------------------

    async def handle_callback(
        self, code: str, state: str,
    ) -> tuple[PlatformAccount, OAuthCredential]:
        """
        处理 OAuth 回调：
        1. 用 code 换取短期 user token
        2. 换取长期 user token
        3. 获取用户管理的 Facebook Page
        4. 从 Page 获取关联的 Instagram Business Account
        5. 获取 IG 账号信息
        """
        async with httpx.AsyncClient(timeout=30) as client:
            # 1. code → 短期 user token
            token_data = await self._exchange_code_for_token(code, client)
            short_lived_token = token_data["access_token"]

            # 2. 短期 → 长期 user token
            long_lived_data = await self._exchange_long_lived_token(
                short_lived_token, client,
            )
            long_lived_token = long_lived_data["access_token"]
            expires_in = long_lived_data.get("expires_in", 5184000)

            # 3. 获取用户的 Pages
            me_resp = await client.get(
                f"{GRAPH_API_BASE}/me",
                params={"access_token": long_lived_token, "fields": "id"},
            )
            if me_resp.status_code != 200:
                raise OAuthError(
                    f"Instagram user info fetch failed: {me_resp.status_code} {me_resp.text}"
                )
            me_data = me_resp.json()
            if "error" in me_data:
                raise OAuthError(
                    f"Instagram user info error: {me_data['error'].get('message', 'unknown')}"
                )
            user_id = me_data["id"]

            pages_resp = await client.get(
                f"{GRAPH_API_BASE}/{user_id}/accounts",
                params={"access_token": long_lived_token},
            )
            if pages_resp.status_code != 200:
                raise OAuthError(
                    f"Instagram pages fetch failed: {pages_resp.status_code} {pages_resp.text}"
                )
            pages_data = pages_resp.json()
            if "error" in pages_data:
                raise OAuthError(
                    f"Instagram pages error: {pages_data['error'].get('message', 'unknown')}"
                )

            pages = pages_data.get("data", [])
            if not pages:
                raise OAuthError("Instagram 未找到关联的 Facebook Page")

            # 4. 从 Page 获取 Instagram Business Account ID
            page = pages[0]
            page_id = page["id"]
            page_token = page["access_token"]

            ig_resp = await client.get(
                f"{GRAPH_API_BASE}/{page_id}",
                params={
                    "fields": "instagram_business_account",
                    "access_token": page_token,
                },
            )
            if ig_resp.status_code != 200:
                raise OAuthError(
                    f"Instagram business account fetch failed: {ig_resp.status_code} {ig_resp.text}"
                )
            ig_data = ig_resp.json()
            if "error" in ig_data:
                raise OAuthError(
                    f"Instagram business account error: {ig_data['error'].get('message', 'unknown')}"
                )

            ig_account = ig_data.get("instagram_business_account")
            if not ig_account:
                raise OAuthError(
                    "Facebook Page 未关联 Instagram Business 账号"
                )
            ig_user_id = ig_account["id"]

            # 5. 获取 IG 账号信息
            account = await self._get_account_info(
                ig_user_id, long_lived_token, client,
            )

        credential = OAuthCredential(
            access_token=long_lived_token,
            refresh_token=long_lived_token,  # Meta 不使用独立 refresh_token
            expires_at=int(time.time()) + expires_in,
            raw=json.dumps({
                "ig_user_id": ig_user_id,
                "page_id": page_id,
                "user_id": user_id,
            }),
        )
        return account, credential

    async def _get_account_info(
        self,
        ig_user_id: str,
        access_token: str,
        client: httpx.AsyncClient,
    ) -> PlatformAccount:
        """获取 Instagram 账号信息。"""
        resp = await client.get(
            f"{GRAPH_API_BASE}/{ig_user_id}",
            params={
                "fields": "id,username,name,profile_picture_url",
                "access_token": access_token,
            },
        )
        if resp.status_code != 200:
            raise OAuthError(
                f"Instagram account info fetch failed: {resp.status_code} {resp.text}"
            )
        data = resp.json()
        if "error" in data:
            raise OAuthError(
                f"Instagram account info error: {data['error'].get('message', 'unknown')}"
            )

        return PlatformAccount(
            platform=PlatformType.INSTAGRAM,
            platform_uid=ig_user_id,
            username=data.get("username", ig_user_id),
            nickname=data.get("name", ""),
            avatar_url=data.get("profile_picture_url", ""),
        )

    # ------------------------------------------------------------------
    # 发布 — Instagram 容器模式（两步流程）
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
        发布视频到 Instagram（容器模式）。

        流程:
        1. 创建 media container（需要视频 URL）
        2. 轮询 container 状态直到 FINISHED
        3. 发布 container

        platform_options 支持:
        - ig_user_id: str (Instagram 用户 ID，默认从 credential.raw 获取)
        - video_url: str (视频的公开 URL，Instagram 需要从 URL 抓取视频)
        - caption: str (帖子文案，默认使用 description)
        - poll_interval: int (轮询间隔秒数，默认 5)
        - poll_timeout: int (轮询超时秒数，默认 300)
        """
        raw_data = json.loads(credential.raw or "{}") if credential.raw else {}
        ig_user_id = platform_options.get("ig_user_id", raw_data.get("ig_user_id", ""))
        video_url = platform_options.get("video_url", "")
        caption = platform_options.get("caption", description)
        poll_interval = platform_options.get("poll_interval", POLL_INTERVAL)
        poll_timeout = platform_options.get("poll_timeout", POLL_TIMEOUT)

        if not ig_user_id:
            raise PublishError("Instagram publish: missing ig_user_id")
        if not video_url:
            raise PublishError("Instagram publish: missing video_url (Instagram requires a public video URL)")

        async with httpx.AsyncClient(timeout=60) as client:
            # 1. 创建 media container
            container_id = await self._create_container(
                ig_user_id, video_url, caption, credential.access_token, client,
            )

            # 2. 轮询 container 状态
            await self._poll_container_status(
                container_id, credential.access_token, client,
                poll_interval=poll_interval,
                poll_timeout=poll_timeout,
            )

            # 3. 发布
            post_id = await self._publish_container(
                ig_user_id, container_id, credential.access_token, client,
            )

        logger.info("Instagram 视频发布成功: post_id=%s", post_id)

        return PublishResult(
            success=True,
            post_id=post_id,
            permalink=f"https://www.instagram.com/p/{post_id}/",
            status="published",
        )

    async def _create_container(
        self,
        ig_user_id: str,
        video_url: str,
        caption: str,
        access_token: str,
        client: httpx.AsyncClient,
    ) -> str:
        """创建 Instagram media container。"""
        resp = await client.post(
            f"{GRAPH_API_BASE}/{ig_user_id}/media",
            data={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "access_token": access_token,
            },
        )
        if resp.status_code != 200:
            raise PublishError(
                f"Instagram container creation failed: {resp.status_code} {resp.text}"
            )
        data = resp.json()
        if "error" in data:
            raise PublishError(
                f"Instagram container error: {data['error'].get('message', 'unknown')}"
            )

        container_id = data.get("id", "")
        if not container_id:
            raise PublishError("Instagram container creation: missing container id")

        logger.info("Instagram container 创建成功: container_id=%s", container_id)
        return container_id

    async def _poll_container_status(
        self,
        container_id: str,
        access_token: str,
        client: httpx.AsyncClient,
        poll_interval: int = POLL_INTERVAL,
        poll_timeout: int = POLL_TIMEOUT,
    ) -> str:
        """
        轮询 container 状态直到 FINISHED。

        状态: IN_PROGRESS → FINISHED / ERROR
        """
        start_time = time.time()

        while True:
            elapsed = time.time() - start_time
            if elapsed > poll_timeout:
                raise PublishError(
                    f"Instagram container polling timeout after {poll_timeout}s "
                    f"(container_id={container_id})"
                )

            resp = await client.get(
                f"{GRAPH_API_BASE}/{container_id}",
                params={
                    "fields": "status_code",
                    "access_token": access_token,
                },
            )
            if resp.status_code != 200:
                raise PublishError(
                    f"Instagram container status check failed: {resp.status_code}"
                )
            data = resp.json()
            if "error" in data:
                raise PublishError(
                    f"Instagram container status error: {data['error'].get('message', 'unknown')}"
                )

            status_code = data.get("status_code", "")
            logger.info(
                "Instagram container 状态: %s (elapsed=%.1fs)",
                status_code, elapsed,
            )

            if status_code == "FINISHED":
                return status_code
            elif status_code == "ERROR":
                raise PublishError(
                    f"Instagram container processing failed (container_id={container_id})"
                )

            await asyncio.sleep(poll_interval)

    async def _publish_container(
        self,
        ig_user_id: str,
        container_id: str,
        access_token: str,
        client: httpx.AsyncClient,
    ) -> str:
        """发布已处理完成的 container。"""
        resp = await client.post(
            f"{GRAPH_API_BASE}/{ig_user_id}/media_publish",
            data={
                "creation_id": container_id,
                "access_token": access_token,
            },
        )
        if resp.status_code != 200:
            raise PublishError(
                f"Instagram publish failed: {resp.status_code} {resp.text}"
            )
        data = resp.json()
        if "error" in data:
            raise PublishError(
                f"Instagram publish error: {data['error'].get('message', 'unknown')}"
            )

        post_id = data.get("id", "")
        if not post_id:
            raise PublishError("Instagram publish: missing post id in response")

        return post_id
