"""
Meta 系平台共享基类（Facebook + Instagram）。

Facebook 和 Instagram 都使用 Meta 的 OAuth 和 Graph API，
因此将共享的认证逻辑抽取到此基类中。
"""

import json
import logging
import time
from abc import abstractmethod
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
from .exceptions import OAuthError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AUTH_URI = "https://www.facebook.com/v18.0/dialog/oauth"
TOKEN_URI = "https://graph.facebook.com/v18.0/oauth/access_token"
DEBUG_TOKEN_URI = "https://graph.facebook.com/debug_token"


class MetaBaseService(PlatformService):
    """Meta 系平台共享基类（Facebook + Instagram）。"""

    AUTH_URI = AUTH_URI
    TOKEN_URI = TOKEN_URI
    DEBUG_TOKEN_URI = DEBUG_TOKEN_URI

    auth_method = AuthMethod.OAUTH2

    # 子类必须覆盖
    platform: PlatformType
    SCOPES: str = ""

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
    # OAuth — 共享逻辑
    # ------------------------------------------------------------------

    async def get_auth_url(self, state: str, **kwargs) -> str:
        """生成 Meta OAuth2 授权 URL。"""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": self.SCOPES,
            "state": state,
        }
        return f"{self.AUTH_URI}?{urlencode(params)}"

    async def _exchange_code_for_token(
        self, code: str, client: httpx.AsyncClient,
    ) -> dict:
        """用 authorization code 换取短期 access_token。"""
        resp = await client.get(
            self.TOKEN_URI,
            params={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "code": code,
            },
        )
        if resp.status_code != 200:
            raise OAuthError(
                f"Meta token exchange failed: {resp.status_code} {resp.text}"
            )
        data = resp.json()
        if "error" in data:
            raise OAuthError(
                f"Meta token exchange error: {data['error'].get('message', 'unknown')}"
            )
        return data

    async def _exchange_long_lived_token(
        self, short_lived_token: str, client: httpx.AsyncClient,
    ) -> dict:
        """将短期 token 换取长期 token（约 60 天）。"""
        resp = await client.get(
            self.TOKEN_URI,
            params={
                "grant_type": "fb_exchange_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "fb_exchange_token": short_lived_token,
            },
        )
        if resp.status_code != 200:
            raise OAuthError(
                f"Meta long-lived token exchange failed: {resp.status_code} {resp.text}"
            )
        data = resp.json()
        if "error" in data:
            raise OAuthError(
                f"Meta long-lived token error: {data['error'].get('message', 'unknown')}"
            )
        return data

    async def refresh_token(
        self, credential: OAuthCredential,
    ) -> OAuthCredential:
        """
        刷新 Meta token。

        Meta 不使用传统的 refresh_token 机制，
        而是通过长期 token 再次交换来延长有效期。
        """
        async with httpx.AsyncClient(timeout=30) as client:
            data = await self._exchange_long_lived_token(
                credential.refresh_token, client,
            )

        new_token = data["access_token"]
        expires_in = data.get("expires_in", 5184000)  # 默认 60 天

        return OAuthCredential(
            access_token=new_token,
            refresh_token=credential.refresh_token,
            expires_at=int(time.time()) + expires_in,
            raw=credential.raw,
        )

    async def check_token_status(
        self, credential: OAuthCredential,
    ) -> bool:
        """检查 token 是否在 600 秒内仍然有效。"""
        return credential.expires_at - time.time() > 600

    # ------------------------------------------------------------------
    # 子类必须实现的抽象方法
    # ------------------------------------------------------------------

    @abstractmethod
    async def _get_account_info(
        self, access_token: str, client: httpx.AsyncClient,
    ) -> PlatformAccount:
        """获取平台特定的用户/账号信息（子类实现）。"""
        ...
