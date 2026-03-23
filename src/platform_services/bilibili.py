"""
Bilibili 平台服务实现。

使用 Bilibili 开放平台 OAuth2 进行授权，分片上传协议发布视频。
"""

import hashlib
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

AUTH_URI = "https://passport.bilibili.com/register/pc_oauth2.html"
TOKEN_URI = "https://api.bilibili.com/x/account-oauth2/v1/token"
REFRESH_TOKEN_URI = "https://api.bilibili.com/x/account-oauth2/v1/refresh_token"
USER_INFO_URI = "https://member.bilibili.com/x2/creative/h5/upload/member/info"

# 视频上传相关
PRE_UPLOAD_URI = "https://member.bilibili.com/preupload"
VIDEO_SUBMIT_URI = "https://member.bilibili.com/x/vu/client/add"

# 默认分片大小：4 MB
DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024


class BilibiliService(PlatformService):
    """Bilibili 平台服务（Bilibili 开放平台 OAuth2）。"""

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
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "state": state,
        }
        return f"{AUTH_URI}?{urlencode(params)}"

    async def handle_callback(
        self, code: str, state: str,
    ) -> tuple[PlatformAccount, OAuthCredential]:
        """用授权码换取 token 并获取用户信息（mid, name, face）。"""
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
            resp_json = token_resp.json()

            # Bilibili API 返回 {"code": 0, "data": {...}}
            if resp_json.get("code") != 0:
                raise OAuthError(
                    f"Bilibili token exchange error: {resp_json.get('message', 'unknown')}"
                )

            token_data = resp_json["data"]
            access_token = token_data["access_token"]
            refresh_token = token_data["refresh_token"]
            expires_in = token_data.get("expires_in", 86400)
            expires_at = int(time.time()) + expires_in

            # 2. 获取用户信息
            user_resp = await client.get(
                USER_INFO_URI,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if user_resp.status_code != 200:
                raise OAuthError(
                    f"Bilibili user info failed: {user_resp.status_code} {user_resp.text}"
                )
            user_json = user_resp.json()

            if user_json.get("code") != 0:
                raise OAuthError(
                    f"Bilibili user info error: {user_json.get('message', 'unknown')}"
                )

            user_data = user_json["data"]
            mid = str(user_data.get("mid", ""))
            name = user_data.get("name", "")
            face = user_data.get("face", "")

            account = PlatformAccount(
                platform=PlatformType.BILIBILI,
                platform_uid=mid,
                username=mid,
                nickname=name,
                avatar_url=face,
            )
            credential = OAuthCredential(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                raw=json.dumps(token_data),
            )

        return account, credential

    async def refresh_token(
        self, credential: OAuthCredential,
    ) -> OAuthCredential:
        """刷新 access_token（Bilibili 会返回新的 refresh_token）。"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                REFRESH_TOKEN_URI,
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
            resp_json = resp.json()

            if resp_json.get("code") != 0:
                raise OAuthError(
                    f"Bilibili token refresh error: {resp_json.get('message', 'unknown')}"
                )

            data = resp_json["data"]

        return OAuthCredential(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],  # Bilibili 返回新的 refresh_token
            expires_at=int(time.time()) + data.get("expires_in", 86400),
            raw=json.dumps(data),
        )

    async def check_token_status(
        self, credential: OAuthCredential,
    ) -> bool:
        """检查 token 是否仍然有效（距过期 > 600s 视为有效）。"""
        return credential.expires_at - time.time() > 600

    # ------------------------------------------------------------------
    # Publish
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
        上传视频到 Bilibili。

        遵循 Bilibili 分片上传协议：
        1. pre-upload：获取上传 URL 和参数
        2. chunk upload：分片上传视频文件
        3. submit：提交稿件信息
        """
        if not os.path.isfile(video_path):
            raise PublishError(f"Video file not found: {video_path}")

        file_size = os.path.getsize(video_path)
        file_name = os.path.basename(video_path)

        auth_headers = {"Authorization": f"Bearer {credential.access_token}"}

        async with httpx.AsyncClient(timeout=120) as client:
            # 1. Pre-upload: 获取上传端点
            pre_resp = await client.get(
                PRE_UPLOAD_URI,
                params={
                    "name": file_name,
                    "size": file_size,
                    "r": "upos",
                    "profile": "ugcfx/bup",
                },
                headers=auth_headers,
            )
            if pre_resp.status_code != 200:
                raise PublishError(
                    f"Bilibili pre-upload failed: {pre_resp.status_code} {pre_resp.text}"
                )
            pre_data = pre_resp.json()

            # 从 pre-upload 响应中提取上传信息
            upload_url = pre_data.get("url", "")
            complete_url = pre_data.get("complete", "")
            filename = pre_data.get("bili_filename", file_name)
            biz_id = pre_data.get("biz_id", 0)

            if not upload_url:
                raise PublishError(
                    f"Bilibili pre-upload returned no upload URL: {pre_data}"
                )

            # 2. Chunk upload: 分片上传
            chunk_size = platform_options.get("chunk_size", DEFAULT_CHUNK_SIZE)
            total_chunks = (file_size + chunk_size - 1) // chunk_size

            with open(video_path, "rb") as f:
                for chunk_idx in range(total_chunks):
                    chunk_data = f.read(chunk_size)
                    chunk_md5 = hashlib.md5(chunk_data).hexdigest()

                    upload_resp = await client.put(
                        upload_url,
                        params={
                            "partNumber": chunk_idx + 1,
                            "chunk": chunk_idx,
                            "chunks": total_chunks,
                            "size": len(chunk_data),
                            "start": chunk_idx * chunk_size,
                            "end": chunk_idx * chunk_size + len(chunk_data),
                            "total": file_size,
                            "md5": chunk_md5,
                        },
                        content=chunk_data,
                        headers={
                            **auth_headers,
                            "Content-Type": "application/octet-stream",
                        },
                    )
                    if upload_resp.status_code not in (200, 201, 202):
                        raise PublishError(
                            f"Bilibili chunk upload failed at chunk {chunk_idx}: "
                            f"{upload_resp.status_code} {upload_resp.text}"
                        )
                    logger.info(
                        "Bilibili upload progress: chunk %d/%d",
                        chunk_idx + 1,
                        total_chunks,
                    )

            # 3. 通知上传完成
            if complete_url:
                complete_resp = await client.post(
                    complete_url,
                    data={"filename": filename, "chunks": total_chunks},
                    headers=auth_headers,
                )
                if complete_resp.status_code != 200:
                    logger.warning(
                        "Bilibili complete notification returned %d: %s",
                        complete_resp.status_code,
                        complete_resp.text,
                    )

            # 4. Submit: 提交稿件
            submit_data = {
                "cover": cover_path,
                "title": title,
                "tid": platform_options.get("tid", 17),  # 默认分区：单机游戏
                "tag": ",".join(tags) if tags else "",
                "desc": description,
                "copyright": platform_options.get("copyright", 1),  # 1=自制 2=转载
                "source": platform_options.get("source", ""),
                "videos": [
                    {
                        "filename": filename,
                        "title": title,
                        "desc": description,
                    }
                ],
            }

            submit_resp = await client.post(
                VIDEO_SUBMIT_URI,
                json=submit_data,
                headers=auth_headers,
            )
            if submit_resp.status_code != 200:
                raise PublishError(
                    f"Bilibili submit failed: {submit_resp.status_code} {submit_resp.text}"
                )
            submit_json = submit_resp.json()

            if submit_json.get("code") != 0:
                raise PublishError(
                    f"Bilibili submit error: {submit_json.get('message', 'unknown')}"
                )

            result_data = submit_json.get("data", {})
            bvid = result_data.get("bvid", "")
            aid = str(result_data.get("aid", biz_id))

            logger.info("Bilibili upload complete: bvid=%s, aid=%s", bvid, aid)

            return PublishResult(
                success=True,
                post_id=bvid or aid,
                permalink=f"https://www.bilibili.com/video/{bvid}" if bvid else "",
                status="published",
            )
