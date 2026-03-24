"""
Bilibili 平台服务实现。

使用 Bilibili 开放平台 OAuth2 进行认证，分片上传协议发布视频。
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

AUTH_URI = "https://account.bilibili.com/pc/account-pc/auth/oauth"
TOKEN_URI = "https://api.bilibili.com/x/account-oauth2/v1/token"
REFRESH_URI = "https://api.bilibili.com/x/account-oauth2/v1/refresh_token"
USER_INFO_URI = "https://member.bilibili.com/x2/creative/h5/upload/member/info"

# 视频上传相关
PREUPLOAD_URI = "https://member.bilibili.com/preupload"
SUBMIT_URI = "https://member.bilibili.com/x/vu/client/add"

DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB


class BilibiliService(PlatformService):
    """Bilibili 平台服务（Bilibili Open Platform OAuth2）。"""

    platform = PlatformType.BILIBILI
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
        """生成 Bilibili OAuth2 授权 URL。"""
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
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
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.redirect_uri,
                },
            )
            if token_resp.status_code != 200:
                raise OAuthError(
                    f"Bilibili token exchange failed: {token_resp.status_code} {token_resp.text}"
                )
            resp_data = token_resp.json()

            # Bilibili API 将实际数据放在 data 字段中
            if resp_data.get("code") != 0:
                raise OAuthError(
                    f"Bilibili token exchange error: {resp_data.get('message', 'unknown')}"
                )
            token_data = resp_data.get("data", {})

            access_token = token_data["access_token"]
            refresh_token = token_data.get("refresh_token", "")
            expires_in = token_data.get("expires_in", 86400)
            expires_at = int(time.time()) + expires_in

            # 2. 获取用户信息 (mid, name, face)
            user_resp = await client.get(
                USER_INFO_URI,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if user_resp.status_code != 200:
                raise OAuthError(
                    f"Bilibili user info fetch failed: {user_resp.status_code} {user_resp.text}"
                )
            user_data = user_resp.json()
            if user_data.get("code") != 0:
                raise OAuthError(
                    f"Bilibili user info error: {user_data.get('message', 'unknown')}"
                )
            user_info = user_data.get("data", {})

        mid = str(user_info.get("mid", ""))
        name = user_info.get("name", "")
        face = user_info.get("face", "")

        credential = OAuthCredential(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            raw=json.dumps(resp_data),
        )
        account = PlatformAccount(
            platform=PlatformType.BILIBILI,
            platform_uid=mid,
            username=mid,
            nickname=name,
            avatar_url=face,
        )
        return account, credential

    async def refresh_token(
        self, credential: OAuthCredential,
    ) -> OAuthCredential:
        """刷新 access_token（Bilibili 会返回新的 refresh_token）。"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                REFRESH_URI,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": credential.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if resp.status_code != 200:
                raise OAuthError(
                    f"Bilibili token refresh failed: {resp.status_code} {resp.text}"
                )
            resp_data = resp.json()
            if resp_data.get("code") != 0:
                raise OAuthError(
                    f"Bilibili token refresh error: {resp_data.get('message', 'unknown')}"
                )
            token_data = resp_data.get("data", {})

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
        上传视频到 Bilibili（分片上传协议）。

        流程: pre-upload → chunk upload → complete → submit

        platform_options 支持:
        - tid: int (分区 ID, 默认 17 = 单机游戏)
        - copyright: int (1=自制, 2=转载, 默认 2)
        - source: str (转载来源, copyright=2 时需要)
        """
        tid = platform_options.get("tid", 17)
        copyright_val = platform_options.get("copyright", 2)
        source = platform_options.get("source", "")

        file_size = os.path.getsize(video_path)
        file_name = os.path.basename(video_path)

        auth_headers = {"Authorization": f"Bearer {credential.access_token}"}

        async with httpx.AsyncClient(timeout=120) as client:
            # 1. Pre-upload: 获取上传参数
            preupload_resp = await client.get(
                PREUPLOAD_URI,
                params={
                    "name": file_name,
                    "size": file_size,
                    "r": "upos",
                    "profile": "ugcupos/bup",
                },
                headers=auth_headers,
            )
            if preupload_resp.status_code != 200:
                raise PublishError(
                    f"Bilibili pre-upload failed: {preupload_resp.status_code}"
                )
            preupload_data = preupload_resp.json()

            upload_url = preupload_data.get("url", "")
            complete_url = preupload_data.get("complete", "")
            biz_id = preupload_data.get("biz_id", 0)
            upos_uri = preupload_data.get("upos_uri", "")

            if not upload_url:
                raise PublishError("Bilibili pre-upload: missing upload_url")

            # 确保 URL 有 scheme
            if upload_url.startswith("//"):
                upload_url = "https:" + upload_url
            if complete_url.startswith("//"):
                complete_url = "https:" + complete_url

            # 2. 分片上传
            chunk_size = DEFAULT_CHUNK_SIZE
            total_chunks = (file_size + chunk_size - 1) // chunk_size

            with open(video_path, "rb") as f:
                for chunk_idx in range(total_chunks):
                    chunk_data = f.read(chunk_size)
                    chunk_start = chunk_idx * chunk_size
                    chunk_end = chunk_start + len(chunk_data)

                    chunk_resp = await client.put(
                        upload_url,
                        params={
                            "partNumber": chunk_idx + 1,
                            "uploadId": preupload_data.get("upload_id", ""),
                            "chunk": chunk_idx,
                            "chunks": total_chunks,
                            "size": len(chunk_data),
                            "start": chunk_start,
                            "end": chunk_end,
                            "total": file_size,
                        },
                        content=chunk_data,
                        headers={
                            **auth_headers,
                            "Content-Type": "application/octet-stream",
                        },
                    )
                    if chunk_resp.status_code not in (200, 201, 202):
                        raise PublishError(
                            f"Bilibili chunk upload failed at chunk {chunk_idx}: "
                            f"{chunk_resp.status_code}"
                        )
                    logger.info(
                        "Bilibili 上传进度: chunk %d/%d", chunk_idx + 1, total_chunks
                    )

            # 3. Complete upload
            parts = [{"partNumber": i + 1, "eTag": "etag"} for i in range(total_chunks)]
            complete_resp = await client.post(
                complete_url,
                params={
                    "output": "json",
                    "name": file_name,
                    "profile": "ugcupos/bup",
                    "uploadId": preupload_data.get("upload_id", ""),
                    "biz_id": biz_id,
                },
                json={"parts": parts},
                headers=auth_headers,
            )
            if complete_resp.status_code != 200:
                raise PublishError(
                    f"Bilibili complete upload failed: {complete_resp.status_code}"
                )

            # 4. Submit video
            tag_str = ",".join(tags) if tags else ""
            submit_data = {
                "cover": cover_path,
                "title": title,
                "tid": tid,
                "tag": tag_str,
                "desc": description,
                "copyright": copyright_val,
                "source": source,
                "videos": [
                    {
                        "filename": upos_uri.rsplit("/", 1)[-1].split(".")[0] if upos_uri else file_name,
                        "title": title,
                        "desc": "",
                    }
                ],
            }

            submit_resp = await client.post(
                SUBMIT_URI,
                json=submit_data,
                headers=auth_headers,
            )
            if submit_resp.status_code != 200:
                raise PublishError(
                    f"Bilibili submit failed: {submit_resp.status_code}"
                )
            submit_result = submit_resp.json()
            if submit_result.get("code") != 0:
                raise PublishError(
                    f"Bilibili submit error: {submit_result.get('message', 'unknown')}"
                )

            result_data = submit_result.get("data", {})
            bvid = result_data.get("bvid", "")

        logger.info("Bilibili 视频上传成功: bvid=%s", bvid)
        return PublishResult(
            success=True,
            post_id=bvid,
            permalink=f"https://www.bilibili.com/video/{bvid}" if bvid else "",
            status="published",
        )
