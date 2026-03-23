"""
LinkedIn 平台服务实现。

使用 LinkedIn OAuth 2.0 进行认证，UGC Post API 发布视频。
"""

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

AUTH_URI = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URI = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URI = "https://api.linkedin.com/v2/userinfo"
REGISTER_UPLOAD_URI = "https://api.linkedin.com/v2/assets?action=registerUpload"
UGC_POST_URI = "https://api.linkedin.com/v2/ugcPosts"

SCOPES = "w_member_social r_liteprofile"


class LinkedInService(PlatformService):
    """LinkedIn 平台服务（OAuth 2.0 + UGC Post API）。"""

    platform = PlatformType.LINKEDIN
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
        """生成 LinkedIn OAuth2 授权 URL。"""
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": SCOPES,
            "state": state,
        }
        return f"{AUTH_URI}?{urlencode(params)}"

    async def handle_callback(
        self, code: str, state: str,
    ) -> tuple[PlatformAccount, OAuthCredential]:
        """用 authorization code 换取 token 并获取用户信息。"""
        async with httpx.AsyncClient(timeout=30) as client:
            # 1. Exchange code for token
            token_resp = await client.post(
                TOKEN_URI,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            if token_resp.status_code != 200:
                raise OAuthError(
                    f"LinkedIn token exchange failed: {token_resp.status_code} {token_resp.text}"
                )
            token_data = token_resp.json()

            access_token = token_data["access_token"]
            refresh_token = token_data.get("refresh_token", "")
            expires_in = token_data.get("expires_in", 3600)
            expires_at = int(time.time()) + expires_in

            # 2. Get user info
            user_resp = await client.get(
                USERINFO_URI,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if user_resp.status_code != 200:
                raise OAuthError(
                    f"LinkedIn user info fetch failed: {user_resp.status_code} {user_resp.text}"
                )
            user_data = user_resp.json()

        credential = OAuthCredential(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            raw=json.dumps(token_data),
        )
        account = PlatformAccount(
            platform=PlatformType.LINKEDIN,
            platform_uid=user_data.get("sub", ""),
            username=user_data.get("email", user_data.get("sub", "")),
            nickname=user_data.get("name", ""),
            avatar_url=user_data.get("picture", ""),
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
                    "grant_type": "refresh_token",
                    "refresh_token": credential.refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            if resp.status_code != 200:
                raise OAuthError(
                    f"LinkedIn token refresh failed: {resp.status_code} {resp.text}"
                )
            data = resp.json()

        return OAuthCredential(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", credential.refresh_token),
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
        上传视频到 LinkedIn（Register Upload + UGC Post）。

        流程: registerUpload → upload binary → create UGC Post
        """
        auth_headers = {
            "Authorization": f"Bearer {credential.access_token}",
        }

        # Retrieve person URN (needed for author field)
        async with httpx.AsyncClient(timeout=30) as client:
            me_resp = await client.get(
                USERINFO_URI,
                headers=auth_headers,
            )
            if me_resp.status_code != 200:
                raise PublishError(
                    f"LinkedIn get user info failed: {me_resp.status_code}"
                )
            person_id = me_resp.json().get("sub", "")
        person_urn = f"urn:li:person:{person_id}"

        async with httpx.AsyncClient(timeout=120) as client:
            # 1. Register upload
            register_body = {
                "registerUploadRequest": {
                    "recipes": ["urn:li:digitalmediaRecipe:feedshare-video"],
                    "owner": person_urn,
                    "serviceRelationships": [
                        {
                            "relationshipType": "OWNER",
                            "identifier": "urn:li:userGeneratedContent",
                        }
                    ],
                },
            }
            register_resp = await client.post(
                REGISTER_UPLOAD_URI,
                json=register_body,
                headers={
                    **auth_headers,
                    "Content-Type": "application/json",
                },
            )
            if register_resp.status_code not in (200, 201):
                raise PublishError(
                    f"LinkedIn register upload failed: {register_resp.status_code} "
                    f"{register_resp.text}"
                )
            register_data = register_resp.json()
            upload_mechanism = (
                register_data.get("value", {})
                .get("uploadMechanism", {})
                .get("com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest", {})
            )
            upload_url = upload_mechanism.get("uploadUrl", "")
            asset = register_data.get("value", {}).get("asset", "")

            if not upload_url or not asset:
                raise PublishError(
                    "LinkedIn register upload: missing uploadUrl or asset"
                )

            # 2. Upload video binary
            with open(video_path, "rb") as f:
                video_data = f.read()

            upload_resp = await client.put(
                upload_url,
                content=video_data,
                headers={
                    **auth_headers,
                    "Content-Type": "application/octet-stream",
                },
            )
            if upload_resp.status_code not in (200, 201):
                raise PublishError(
                    f"LinkedIn video upload failed: {upload_resp.status_code}"
                )

            # 3. Create UGC Post
            commentary = title
            if description:
                commentary = f"{title}\n\n{description}"
            if tags:
                hashtags = " ".join(f"#{tag}" for tag in tags)
                commentary = f"{commentary}\n{hashtags}"

            ugc_body = {
                "author": person_urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": commentary},
                        "shareMediaCategory": "VIDEO",
                        "media": [
                            {
                                "status": "READY",
                                "media": asset,
                                "title": {"text": title},
                                "description": {"text": description or ""},
                            }
                        ],
                    },
                },
                "visibility": {
                    "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC",
                },
            }

            ugc_resp = await client.post(
                UGC_POST_URI,
                json=ugc_body,
                headers={
                    **auth_headers,
                    "Content-Type": "application/json",
                },
            )
            if ugc_resp.status_code not in (200, 201):
                raise PublishError(
                    f"LinkedIn UGC post creation failed: {ugc_resp.status_code} {ugc_resp.text}"
                )
            ugc_data = ugc_resp.json()
            post_id = ugc_data.get("id", "")

        logger.info("LinkedIn 视频发布成功: post_id=%s", post_id)
        return PublishResult(
            success=True,
            post_id=post_id,
            permalink=f"https://www.linkedin.com/feed/update/{post_id}" if post_id else "",
            status="published",
        )
