"""
微信视频号（Weixin Channels / SPH）平台服务实现。

使用微信开放平台 OAuth2 进行认证。
文档: https://developers.weixin.qq.com/doc/channels/API/
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

AUTH_URI = "https://open.weixin.qq.com/connect/oauth2/authorize"
TOKEN_URI = "https://api.weixin.qq.com/sns/oauth2/access_token"
REFRESH_URI = "https://api.weixin.qq.com/sns/oauth2/refresh_token"
USER_INFO_URI = "https://api.weixin.qq.com/sns/userinfo"

SCOPES = "snsapi_userinfo"


class WeixinChannelsService(PlatformService):
    """微信视频号平台服务（Weixin Open Platform OAuth2）。"""

    platform = PlatformType.WEIXIN_SPH
    auth_method = AuthMethod.COOKIE

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        redirect_uri: str,
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.redirect_uri = redirect_uri

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------

    async def get_auth_url(self, state: str, **kwargs) -> str:
        """生成微信 OAuth2 授权 URL。"""
        params = {
            "appid": self.app_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
        }
        return f"{AUTH_URI}?{urlencode(params)}#wechat_redirect"

    async def handle_callback(
        self, code: str, state: str,
    ) -> tuple[PlatformAccount, OAuthCredential]:
        """用 authorization code 换取 token 并获取用户信息。"""
        async with httpx.AsyncClient(timeout=30) as client:
            # 1. 用 code 换 token
            token_resp = await client.get(
                TOKEN_URI,
                params={
                    "appid": self.app_id,
                    "secret": self.app_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
            )
            if token_resp.status_code != 200:
                raise OAuthError(
                    f"Weixin Channels token exchange failed: {token_resp.status_code} {token_resp.text}"
                )
            token_data = token_resp.json()

            if "errcode" in token_data and token_data["errcode"] != 0:
                raise OAuthError(
                    f"Weixin Channels token exchange error: {token_data.get('errmsg', 'unknown')}"
                )

            access_token = token_data["access_token"]
            refresh_token = token_data.get("refresh_token", "")
            expires_in = token_data.get("expires_in", 7200)
            expires_at = int(time.time()) + expires_in
            openid = token_data.get("openid", "")

            # 2. 获取用户信息
            user_resp = await client.get(
                USER_INFO_URI,
                params={"access_token": access_token, "openid": openid, "lang": "zh_CN"},
            )
            if user_resp.status_code != 200:
                raise OAuthError(
                    f"Weixin Channels user info fetch failed: {user_resp.status_code} {user_resp.text}"
                )
            user_data = user_resp.json()

            if "errcode" in user_data and user_data["errcode"] != 0:
                raise OAuthError(
                    f"Weixin Channels user info error: {user_data.get('errmsg', 'unknown')}"
                )

        credential = OAuthCredential(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            raw=json.dumps(token_data),
        )
        account = PlatformAccount(
            platform=PlatformType.WEIXIN_SPH,
            platform_uid=openid,
            username=user_data.get("nickname", openid),
            nickname=user_data.get("nickname", ""),
            avatar_url=user_data.get("headimgurl", ""),
        )
        return account, credential

    async def refresh_token(
        self, credential: OAuthCredential,
    ) -> OAuthCredential:
        """刷新 access_token。"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                REFRESH_URI,
                params={
                    "appid": self.app_id,
                    "grant_type": "refresh_token",
                    "refresh_token": credential.refresh_token,
                },
            )
            if resp.status_code != 200:
                raise OAuthError(
                    f"Weixin Channels token refresh failed: {resp.status_code} {resp.text}"
                )
            data = resp.json()

            if "errcode" in data and data["errcode"] != 0:
                raise OAuthError(
                    f"Weixin Channels token refresh error: {data.get('errmsg', 'unknown')}"
                )

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
        微信视频号发布（需要通过微信视频号助手或 API）。

        注意：微信视频号的内容发布 API 目前仅对特定合作伙伴开放，
        普通开发者可能需要通过视频号助手手动上传。
        此方法为 API 可用时的预留实现。
        """
        raise PublishError(
            "微信视频号暂不支持通过 API 直接发布视频，请通过视频号助手上传"
        )
