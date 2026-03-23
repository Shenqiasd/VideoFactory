"""
Facebook 平台服务实现。

使用 Meta OAuth2 进行认证，Graph API 进行视频发布。
发布目标为 Facebook Page（非个人时间线）。
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
from .meta_base import MetaBaseService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GRAPH_API_BASE = "https://graph.facebook.com/v18.0"
GRAPH_VIDEO_BASE = "https://graph-video.facebook.com/v18.0"

SCOPES = "pages_manage_posts,pages_read_engagement,publish_video"

# 小于 1 GB 的视频可以使用简单上传
SIMPLE_UPLOAD_LIMIT = 1 * 1024 * 1024 * 1024  # 1 GB


class FacebookService(MetaBaseService):
    """Facebook 平台服务（Meta OAuth2 + Graph API Page 发布）。"""

    platform = PlatformType.FACEBOOK
    SCOPES = SCOPES

    # ------------------------------------------------------------------
    # OAuth — Facebook 特有逻辑
    # ------------------------------------------------------------------

    async def handle_callback(
        self, code: str, state: str,
    ) -> tuple[PlatformAccount, OAuthCredential]:
        """
        处理 OAuth 回调：
        1. 用 code 换取短期 user token
        2. 换取长期 user token
        3. 获取用户管理的 Page 列表，取第一个 Page 的 token
        4. 返回 Page 级别的账号信息和凭证
        """
        async with httpx.AsyncClient(timeout=30) as client:
            # 1. code → 短期 user token
            token_data = await self._exchange_code_for_token(code, client)
            short_lived_token = token_data["access_token"]

            # 2. 短期 → 长期 user token
            long_lived_data = await self._exchange_long_lived_token(
                short_lived_token, client,
            )
            long_lived_user_token = long_lived_data["access_token"]
            expires_in = long_lived_data.get("expires_in", 5184000)

            # 3. 获取用户 ID
            me_resp = await client.get(
                f"{GRAPH_API_BASE}/me",
                params={"access_token": long_lived_user_token, "fields": "id,name"},
            )
            if me_resp.status_code != 200:
                raise OAuthError(
                    f"Facebook user info fetch failed: {me_resp.status_code} {me_resp.text}"
                )
            me_data = me_resp.json()
            if "error" in me_data:
                raise OAuthError(
                    f"Facebook user info error: {me_data['error'].get('message', 'unknown')}"
                )
            user_id = me_data["id"]

            # 4. User Token → Page Token
            pages_resp = await client.get(
                f"{GRAPH_API_BASE}/{user_id}/accounts",
                params={"access_token": long_lived_user_token},
            )
            if pages_resp.status_code != 200:
                raise OAuthError(
                    f"Facebook pages fetch failed: {pages_resp.status_code} {pages_resp.text}"
                )
            pages_data = pages_resp.json()
            if "error" in pages_data:
                raise OAuthError(
                    f"Facebook pages error: {pages_data['error'].get('message', 'unknown')}"
                )

            pages = pages_data.get("data", [])
            if not pages:
                raise OAuthError("Facebook 未找到关联的 Page")

            page = pages[0]
            page_id = page["id"]
            page_name = page.get("name", "")
            page_token = page["access_token"]

            # 5. 获取 Page 头像
            account = await self._get_account_info(
                page_id, page_name, page_token, client,
            )

        credential = OAuthCredential(
            access_token=page_token,
            refresh_token=long_lived_user_token,  # 保留 user token 用于刷新
            expires_at=int(time.time()) + expires_in,
            raw=json.dumps({"page_id": page_id, "user_id": user_id}),
        )
        return account, credential

    async def _get_account_info(
        self,
        page_id: str,
        page_name: str,
        page_token: str,
        client: httpx.AsyncClient,
    ) -> PlatformAccount:
        """获取 Facebook Page 的账号信息。"""
        pic_resp = await client.get(
            f"{GRAPH_API_BASE}/{page_id}/picture",
            params={"redirect": "false", "access_token": page_token},
        )
        avatar_url = ""
        if pic_resp.status_code == 200:
            pic_data = pic_resp.json()
            avatar_url = pic_data.get("data", {}).get("url", "")

        return PlatformAccount(
            platform=PlatformType.FACEBOOK,
            platform_uid=page_id,
            username=page_id,
            nickname=page_name,
            avatar_url=avatar_url,
        )

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
        发布视频到 Facebook Page。

        使用 Graph API 的视频上传端点。
        小文件使用简单上传，大文件使用 resumable upload。

        platform_options 支持:
        - page_id: str (发布目标 Page ID，默认从 credential.raw 中获取)
        """
        raw_data = json.loads(credential.raw or "{}") if credential.raw else {}
        page_id = platform_options.get("page_id", raw_data.get("page_id", ""))

        if not page_id:
            raise PublishError("Facebook publish: missing page_id")

        file_size = os.path.getsize(video_path)

        if file_size < SIMPLE_UPLOAD_LIMIT:
            return await self._simple_upload(
                credential, page_id, video_path, title, description,
            )
        else:
            return await self._resumable_upload(
                credential, page_id, video_path, title, description,
            )

    async def _simple_upload(
        self,
        credential: OAuthCredential,
        page_id: str,
        video_path: str,
        title: str,
        description: str,
    ) -> PublishResult:
        """简单视频上传（适用于小文件）。"""
        upload_url = f"{GRAPH_VIDEO_BASE}/{page_id}/videos"

        async with httpx.AsyncClient(timeout=300) as client:
            with open(video_path, "rb") as f:
                files = {"source": (os.path.basename(video_path), f, "video/mp4")}
                data = {
                    "title": title,
                    "description": description,
                    "access_token": credential.access_token,
                }
                resp = await client.post(upload_url, data=data, files=files)

            if resp.status_code != 200:
                raise PublishError(
                    f"Facebook video upload failed: {resp.status_code} {resp.text}"
                )
            result = resp.json()
            if "error" in result:
                raise PublishError(
                    f"Facebook video upload error: {result['error'].get('message', 'unknown')}"
                )

        video_id = result.get("id", "")
        logger.info("Facebook 视频上传成功: video_id=%s", video_id)

        return PublishResult(
            success=True,
            post_id=video_id,
            permalink=f"https://www.facebook.com/{page_id}/videos/{video_id}",
            status="published",
        )

    async def _resumable_upload(
        self,
        credential: OAuthCredential,
        page_id: str,
        video_path: str,
        title: str,
        description: str,
    ) -> PublishResult:
        """
        Resumable 视频上传（适用于大文件）。

        流程:
        1. 初始化上传会话
        2. 分片上传
        3. 完成上传
        """
        file_size = os.path.getsize(video_path)
        upload_url = f"{GRAPH_VIDEO_BASE}/{page_id}/videos"

        async with httpx.AsyncClient(timeout=300) as client:
            # 1. 初始化
            init_resp = await client.post(
                upload_url,
                data={
                    "upload_phase": "start",
                    "file_size": str(file_size),
                    "access_token": credential.access_token,
                },
            )
            if init_resp.status_code != 200:
                raise PublishError(
                    f"Facebook resumable init failed: {init_resp.status_code} {init_resp.text}"
                )
            init_data = init_resp.json()
            if "error" in init_data:
                raise PublishError(
                    f"Facebook resumable init error: {init_data['error'].get('message', 'unknown')}"
                )

            upload_session_id = init_data.get("upload_session_id", "")
            start_offset = int(init_data.get("start_offset", 0))
            end_offset = int(init_data.get("end_offset", file_size))

            # 2. 分片上传
            with open(video_path, "rb") as f:
                while start_offset < file_size:
                    chunk_size = end_offset - start_offset
                    f.seek(start_offset)
                    chunk = f.read(chunk_size)

                    transfer_resp = await client.post(
                        upload_url,
                        data={
                            "upload_phase": "transfer",
                            "upload_session_id": upload_session_id,
                            "start_offset": str(start_offset),
                            "access_token": credential.access_token,
                        },
                        files={"video_file_chunk": ("chunk", chunk, "application/octet-stream")},
                    )
                    if transfer_resp.status_code != 200:
                        raise PublishError(
                            f"Facebook chunk upload failed: {transfer_resp.status_code}"
                        )
                    transfer_data = transfer_resp.json()
                    if "error" in transfer_data:
                        raise PublishError(
                            f"Facebook chunk error: {transfer_data['error'].get('message', 'unknown')}"
                        )

                    start_offset = int(transfer_data.get("start_offset", file_size))
                    end_offset = int(transfer_data.get("end_offset", file_size))

                    progress = min(start_offset / file_size * 100, 100)
                    logger.info("Facebook 上传进度: %.1f%%", progress)

            # 3. 完成上传
            finish_resp = await client.post(
                upload_url,
                data={
                    "upload_phase": "finish",
                    "upload_session_id": upload_session_id,
                    "title": title,
                    "description": description,
                    "access_token": credential.access_token,
                },
            )
            if finish_resp.status_code != 200:
                raise PublishError(
                    f"Facebook upload finish failed: {finish_resp.status_code} {finish_resp.text}"
                )
            finish_data = finish_resp.json()
            if "error" in finish_data:
                raise PublishError(
                    f"Facebook upload finish error: {finish_data['error'].get('message', 'unknown')}"
                )

        video_id = finish_data.get("video_id", "")
        logger.info("Facebook 视频上传成功 (resumable): video_id=%s", video_id)

        return PublishResult(
            success=True,
            post_id=video_id,
            permalink=f"https://www.facebook.com/{page_id}/videos/{video_id}",
            status="published",
        )
