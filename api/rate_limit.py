"""
API 速率限制模块
使用 slowapi 对高风险写入接口进行频率控制，防止滥用付费翻译/TTS API 配额。
"""
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse


def _get_identifier(request: Request) -> str:
    """
    提取客户端标识（IP），优先使用 X-Forwarded-For 头部（反向代理场景），
    否则回退到 slowapi 默认的 get_remote_address。
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # X-Forwarded-For 可能包含多个 IP，取第一个（真实客户端 IP）
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


# 全局 Limiter 实例（内存存储，适用于单实例部署）
limiter = Limiter(key_func=_get_identifier, storage_uri="memory://")


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """
    自定义速率限制错误处理器，返回结构化 JSON 而非纯文本。
    """
    return JSONResponse(
        status_code=429,
        content={
            "code": "RATE_LIMITED",
            "message": f"请求频率超限，请稍后再试。限制: {exc.detail}",
        },
    )
