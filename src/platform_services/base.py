"""
平台服务抽象基类 + 核心数据类。

所有平台（YouTube、Bilibili、TikTok 等）都必须继承 PlatformService 并实现其抽象方法。
设计参考 AiToEarn 的 PlatformBaseService。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

class PlatformType(str, Enum):
    """支持的平台类型。"""
    YOUTUBE = "youtube"
    BILIBILI = "bilibili"
    TIKTOK = "tiktok"
    DOUYIN = "douyin"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    THREADS = "threads"
    TWITTER = "twitter"
    PINTEREST = "pinterest"
    LINKEDIN = "linkedin"
    KWAI = "kwai"
    XIAOHONGSHU = "xiaohongshu"
    WEIXIN_SPH = "weixin_sph"
    WEIXIN_GZH = "weixin_gzh"


class AuthMethod(str, Enum):
    """认证方式。"""
    OAUTH2 = "oauth2"
    COOKIE = "cookie"


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class OAuthCredential:
    """OAuth 凭证。"""
    access_token: str
    refresh_token: str
    expires_at: int                       # Unix timestamp（秒）
    refresh_expires_at: Optional[int] = None
    raw: Optional[str] = None             # 原始响应 JSON（调试用）


@dataclass
class PlatformAccount:
    """平台账号信息（从 OAuth 回调或 API 获取）。"""
    platform: PlatformType
    platform_uid: str
    username: str
    nickname: str
    avatar_url: str = ""


@dataclass
class PublishResult:
    """发布结果。"""
    success: bool
    post_id: str = ""
    permalink: str = ""
    error: str = ""
    status: str = "published"             # published / publishing / failed


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------

class PlatformService(ABC):
    """
    所有平台服务的抽象基类。

    每个平台实现（如 YouTubeService）必须：
    1. 设置 platform 和 auth_method 类属性
    2. 实现下列 5 个核心抽象方法
    """

    platform: PlatformType
    auth_method: AuthMethod = AuthMethod.OAUTH2

    @abstractmethod
    async def get_auth_url(self, state: str, **kwargs) -> str:
        """生成 OAuth 授权 URL。"""
        ...

    @abstractmethod
    async def handle_callback(
        self, code: str, state: str,
    ) -> tuple[PlatformAccount, OAuthCredential]:
        """处理 OAuth 回调，返回 (账号信息, 凭证)。"""
        ...

    @abstractmethod
    async def refresh_token(
        self, credential: OAuthCredential,
    ) -> OAuthCredential:
        """刷新 access_token，返回新凭证。"""
        ...

    @abstractmethod
    async def check_token_status(
        self, credential: OAuthCredential,
    ) -> bool:
        """检查 token 是否仍然有效（True = 有效）。"""
        ...

    @abstractmethod
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
        """发布视频到平台。"""
        ...

    # ------ 可选方法（子类按需覆盖） ------

    async def get_account_info(
        self, credential: OAuthCredential,
    ) -> PlatformAccount:
        """获取当前授权用户的账号信息。"""
        raise NotImplementedError(
            f"{self.__class__.__name__} 未实现 get_account_info"
        )

    async def delete_post(
        self, credential: OAuthCredential, post_id: str,
    ) -> bool:
        """删除已发布的内容。"""
        raise NotImplementedError(
            f"{self.__class__.__name__} 未实现 delete_post"
        )

    async def get_video_stats(
        self, credential: OAuthCredential, post_id: str,
    ) -> dict:
        """获取视频数据（播放量、点赞等）。"""
        raise NotImplementedError(
            f"{self.__class__.__name__} 未实现 get_video_stats"
        )
