"""
平台相关自定义异常。
"""


class PlatformError(Exception):
    """所有平台异常的基类。"""


class TokenExpiredError(PlatformError):
    """Token 过期或不存在。"""


class OAuthError(PlatformError):
    """OAuth 流程中的错误（授权失败、回调无效等）。"""


class PublishError(PlatformError):
    """发布过程中的错误（上传失败、API 拒绝等）。"""
