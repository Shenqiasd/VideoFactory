"""
OAuth 认证路由 — 多平台统一 OAuth 流程。

提供：
- GET  /oauth/platforms             已注册平台列表
- GET  /oauth/all-platforms          所有支持平台（含未配置）
- GET  /oauth/authorize/{platform}   发起 OAuth 授权（302 重定向）
- POST /oauth/connect/{platform}     发起 OAuth（返回 JSON，用于弹窗）
- GET  /oauth/connect-status/{state} 轮询 OAuth 回调完成状态
- GET  /oauth/callback/{platform}    处理 OAuth 回调
- GET  /oauth/accounts               已绑定账号列表
- GET  /oauth/accounts/{id}          单个账号详情
- DELETE /oauth/accounts/{id}        解绑账号
"""

import json
import logging
import secrets
import time
import uuid
from html import escape
from typing import Optional

from cachetools import TTLCache
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from api.auth import require_auth
from core.database import Database
from platform_services.registry import PlatformRegistry
from platform_services.exceptions import OAuthError, PlatformError

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])

# Public router for endpoints that must NOT require auth (e.g. OAuth callbacks).
# OAuth callbacks are called via redirect from external providers (Google, etc.)
# and may not carry session cookies reliably across cross-site redirects.
# Security is provided by the one-time state token instead.
public_router = APIRouter()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_db: Optional[Database] = None


def _get_db() -> Database:
    """懒初始化数据库实例。"""
    global _db
    if _db is None:
        _db = Database()
    return _db


# ---------------------------------------------------------------------------
# OAuth state 管理（进程内，简易实现）
# 生产环境建议改用 Redis 或签名 token
# ---------------------------------------------------------------------------

_oauth_states: TTLCache = TTLCache(maxsize=10000, ttl=600)

# OAuth 完成状态缓存：state → {"success": bool, "platform": str, "account_id": str, ...}
_oauth_completions: TTLCache = TTLCache(maxsize=10000, ttl=600)


def _create_oauth_state(platform: str) -> str:
    """创建一个带随机 nonce 的 OAuth state 并缓存。"""
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {"platform": platform}
    return state


def _validate_oauth_state(state: str) -> Optional[dict]:
    """验证并消费一个 OAuth state（一次性）。"""
    return _oauth_states.pop(state, None)


# ---------------------------------------------------------------------------
# 全平台元数据（硬编码，与 AiToEarn 对齐）
# ---------------------------------------------------------------------------

ALL_PLATFORMS = [
    {"platform": "youtube",    "label": "YouTube",   "icon": "youtube",         "color": "#FF0000", "auth_type": "oauth2"},
    {"platform": "bilibili",   "label": "Bilibili",  "icon": "tv",              "color": "#00A1D6", "auth_type": "oauth2"},
    {"platform": "tiktok",     "label": "TikTok",    "icon": "music",           "color": "#000000", "auth_type": "oauth2"},
    {"platform": "douyin",     "label": "抖音",       "icon": "music-2",         "color": "#000000", "auth_type": "oauth2"},
    {"platform": "facebook",   "label": "Facebook",  "icon": "facebook",        "color": "#1877F2", "auth_type": "oauth2"},
    {"platform": "instagram",  "label": "Instagram", "icon": "instagram",       "color": "#E4405F", "auth_type": "oauth2"},
    {"platform": "twitter",    "label": "X (Twitter)","icon": "twitter",        "color": "#000000", "auth_type": "oauth2"},
    {"platform": "pinterest",  "label": "Pinterest", "icon": "image",           "color": "#BD081C", "auth_type": "oauth2"},
    {"platform": "linkedin",   "label": "LinkedIn",  "icon": "linkedin",        "color": "#0A66C2", "auth_type": "oauth2"},
    {"platform": "kwai",       "label": "快手",       "icon": "video",           "color": "#FF4906", "auth_type": "oauth2"},
    {"platform": "xiaohongshu","label": "小红书",     "icon": "book-open",       "color": "#FE2C55", "auth_type": "cookie"},
    {"platform": "weixin_sph", "label": "微信视频号",  "icon": "message-circle", "color": "#07C160", "auth_type": "cookie"},
    {"platform": "weixin_gzh", "label": "微信公众号",  "icon": "newspaper",      "color": "#07C160", "auth_type": "cookie"},
    {"platform": "threads",    "label": "Threads",   "icon": "at-sign",         "color": "#000000", "auth_type": "oauth2"},
]

_PLATFORM_LOOKUP = {p["platform"]: p for p in ALL_PLATFORMS}

# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.get("/platforms")
async def list_platforms():
    """列出所有已注册的平台。"""
    platforms = PlatformRegistry.list_platforms()
    return {"success": True, "data": platforms}


@router.get("/all-platforms")
async def list_all_platforms():
    """列出所有支持的平台（含未配置），标注 configured 状态。"""
    registered = {p["platform"] for p in PlatformRegistry.list_platforms()}
    result = []
    for p in ALL_PLATFORMS:
        # Cookie 平台不需要 OAuth 凭证，始终标记为已配置
        is_cookie = p.get("auth_type") == "cookie"
        result.append({
            **p,
            "configured": is_cookie or p["platform"] in registered,
        })
    return {"success": True, "data": result}


# Cookie 认证平台集合
COOKIE_AUTH_PLATFORMS = {p["platform"] for p in ALL_PLATFORMS if p.get("auth_type") == "cookie"}


@router.post("/connect/{platform}")
async def connect_platform(platform: str, request: Request):
    """
    发起 OAuth 连接（JSON 模式，用于弹窗流程）。

    返回 {auth_url, state} 而非 302 重定向，前端用 window.open() 打开。
    Cookie 认证平台不支持此接口，返回提示。
    """
    # Cookie 平台不走 OAuth 流程
    if platform in COOKIE_AUTH_PLATFORMS:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "detail": f"平台 '{platform}' 使用 Cookie 认证，请使用 Cookie 登录接口",
                "auth_type": "cookie",
            },
        )

    service = PlatformRegistry.get(platform)
    if not service:
        return JSONResponse(
            status_code=404,
            content={"success": False, "detail": f"平台 '{platform}' 未注册或不支持"},
        )

    state = _create_oauth_state(platform)
    try:
        auth_url = await service.get_auth_url(state=state)
    except PlatformError as e:
        _validate_oauth_state(state)
        logger.error("生成授权 URL 失败: platform=%s, error=%s", platform, e)
        return JSONResponse(
            status_code=500,
            content={"success": False, "detail": f"生成授权 URL 失败: {e}"},
        )

    return {"success": True, "auth_url": auth_url, "state": state}


@router.get("/connect-status/{state}")
async def connect_status(state: str):
    """
    轮询 OAuth 连接完成状态。

    前端在弹窗打开后每 2 秒调用一次，检查 OAuth 回调是否已完成。
    """
    completion = _oauth_completions.get(state)
    if completion is None:
        return {"success": True, "completed": False}
    return {"success": True, "completed": True, **completion}


@router.get("/authorize/{platform}")
async def authorize(platform: str, request: Request):
    """
    发起 OAuth 授权。

    前端将用户重定向到此端点，后端生成平台 OAuth URL 并 302 重定向。
    """
    # Cookie 平台不走 OAuth 流程
    if platform in COOKIE_AUTH_PLATFORMS:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "detail": f"平台 '{platform}' 使用 Cookie 认证，请使用 Cookie 登录接口",
                "auth_type": "cookie",
            },
        )

    service = PlatformRegistry.get(platform)
    if not service:
        return JSONResponse(
            status_code=404,
            content={"success": False, "detail": f"平台 '{platform}' 未注册或不支持"},
        )

    state = _create_oauth_state(platform)
    try:
        auth_url = await service.get_auth_url(state=state)
    except PlatformError as e:
        _validate_oauth_state(state)  # 清理已创建的 state，防止内存泄漏
        logger.error("生成授权 URL 失败: platform=%s, error=%s", platform, e)
        return JSONResponse(
            status_code=500,
            content={"success": False, "detail": f"生成授权 URL 失败: {e}"},
        )

    return RedirectResponse(url=auth_url, status_code=302)


@public_router.get("/callback/{platform}")
async def callback(
    platform: str,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """
    处理 OAuth 回调。

    平台授权后重定向到此端点，后端用 code 换取 token，
    创建/更新平台账号和凭证，然后重定向到前端账号页面。
    若用户在平台授权页拒绝授权，平台会携带 error 参数回调（无 code）。
    """
    # 0. 处理用户拒绝授权或缺少必要参数
    if error or not code or not state:
        reason = error or "missing_params"
        logger.warning("OAuth 授权被拒绝或参数缺失: platform=%s, error=%s", platform, reason)
        raw_state = state
        if state:
            _validate_oauth_state(state)  # 消费 state，防止重放和内存泄漏
        # 如果有 state，写入完成缓存让轮询端点能感知到失败
        if raw_state:
            _oauth_completions[raw_state] = {"status": "denied", "platform": platform, "reason": reason}
        return _build_callback_response(success=False, platform=platform, reason=reason)

    # 1. 验证 state
    state_data = _validate_oauth_state(state)
    if not state_data or state_data.get("platform") != platform:
        _oauth_completions[state] = {"status": "error", "platform": platform, "reason": "无效的 OAuth state，可能已过期"}
        return _build_callback_response(success=False, platform=platform, reason="无效的 OAuth state，可能已过期")

    # 2. 获取平台服务
    service = PlatformRegistry.get(platform)
    if not service:
        _oauth_completions[state] = {"status": "error", "platform": platform, "reason": f"平台 '{platform}' 未注册"}
        return _build_callback_response(success=False, platform=platform, reason=f"平台 '{platform}' 未注册")

    # 3. 用 code 换 token + 获取用户信息
    try:
        account_info, credential = await service.handle_callback(code=code, state=state)
    except PlatformError as e:
        logger.error("OAuth 回调失败: platform=%s, error=%s", platform, e)
        _oauth_completions[state] = {"status": "error", "platform": platform, "reason": str(e)}
        return _build_callback_response(success=False, platform=platform, reason=str(e))

    # 4. 创建/更新账号
    db = _get_db()
    existing = db.get_platform_account_by_uid(platform, account_info.platform_uid)
    if existing:
        account_id = existing["id"]
        db.update_platform_account(
            account_id,
            username=account_info.username,
            nickname=account_info.nickname,
            avatar_url=account_info.avatar_url,
            status="active",
        )
    else:
        account_id = str(uuid.uuid4())
        db.insert_platform_account({
            "id": account_id,
            "platform": platform,
            "auth_method": service.auth_method.value,
            "platform_uid": account_info.platform_uid,
            "username": account_info.username,
            "nickname": account_info.nickname,
            "avatar_url": account_info.avatar_url,
            "status": "active",
        })

    # 5. 保存凭证
    db.upsert_oauth_credential(
        account_id=account_id,
        platform=platform,
        access_token=credential.access_token,
        refresh_token=credential.refresh_token,
        expires_at=credential.expires_at,
        refresh_expires_at=credential.refresh_expires_at,
        raw=credential.raw or "",
    )

    logger.info(
        "OAuth 绑定成功: platform=%s, uid=%s, nickname=%s",
        platform, account_info.platform_uid, account_info.nickname,
    )

    # 6. 写入完成缓存 + 返回弹窗关闭页面
    _oauth_completions[state] = {
        "status": "success",
        "platform": platform,
        "account_id": account_id,
        "nickname": account_info.nickname,
        "avatar_url": account_info.avatar_url or "",
    }
    return _build_callback_response(success=True, platform=platform)


def _build_callback_response(*, success: bool, platform: str, reason: str = "") -> HTMLResponse:
    """构建 OAuth 回调响应：弹窗模式返回自动关闭 HTML，否则重定向。"""
    safe_label = escape(_PLATFORM_LOOKUP.get(platform, {}).get('label', platform))
    if success:
        title = "授权成功"
        message = f"{safe_label} 账号绑定成功！"
        color = "#16a34a"
    else:
        title = "授权失败"
        message = f"授权被拒绝: {escape(reason)}" if reason else "授权失败"
        color = "#dc2626"

    # Escape platform for JS context: json.dumps produces a quoted string,
    # then replace '</' to prevent </script> injection (XSS).
    # Extracted from f-string because Python < 3.12 forbids backslashes
    # inside f-string expressions.
    safe_platform_js = json.dumps(platform).replace("</", r"<\/")
    safe_reason_js = json.dumps(reason).replace("</", r"<\/")
    success_js = "true" if success else "false"
    icon_char = "✓" if success else "✗"
    fallback_oauth = "success" if success else "denied"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; display: flex; align-items: center;
         justify-content: center; height: 100vh; margin: 0; background: #fafafa; }}
  .card {{ text-align: center; padding: 40px; border-radius: 16px;
           background: white; box-shadow: 0 4px 24px rgba(0,0,0,0.08); }}
  .icon {{ font-size: 48px; margin-bottom: 16px; }}
  .msg {{ color: {color}; font-size: 16px; font-weight: 500; }}
  .sub {{ color: #888; font-size: 13px; margin-top: 8px; }}
</style></head>
<body><div class="card">
  <div class="icon">{icon_char}</div>
  <div class="msg">{message}</div>
  <div class="sub">此窗口将自动关闭...</div>
</div>
<script>
  // 通知父窗口（如果存在）
  if (window.opener) {{
    try {{ window.opener.postMessage({{type: 'oauth-callback', success: {success_js}, platform: {safe_platform_js}, reason: {safe_reason_js}}}, '*'); }} catch(e) {{}}
  }}
  // 2 秒后自动关闭弹窗
  setTimeout(function() {{
    if (window.opener) {{ window.close(); }}
    else {{ window.location.href = '/platform-accounts?oauth={fallback_oauth}'; }}
  }}, 2000);
</script></body></html>"""
    return HTMLResponse(content=html)


@router.get("/accounts")
async def list_accounts(platform: Optional[str] = None):
    """列出已绑定的平台账号。"""
    db = _get_db()
    accounts = db.get_platform_accounts(platform=platform)
    # 脱敏：不返回 cookie_path
    for acc in accounts:
        acc.pop("cookie_path", None)
    return {"success": True, "data": accounts}


@router.get("/accounts/{account_id}")
async def get_account(account_id: str):
    """获取单个平台账号详情。"""
    db = _get_db()
    account = db.get_platform_account(account_id)
    if not account:
        return JSONResponse(
            status_code=404,
            content={"success": False, "detail": "账号不存在"},
        )
    account.pop("cookie_path", None)
    return {"success": True, "data": account}


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str):
    """解绑平台账号（同时删除 OAuth 凭证）。"""
    db = _get_db()
    account = db.get_platform_account(account_id)
    if not account:
        return JSONResponse(
            status_code=404,
            content={"success": False, "detail": "账号不存在"},
        )
    db.delete_oauth_credential(account_id)
    db.delete_platform_account(account_id)
    logger.info("已解绑账号: id=%s, platform=%s", account_id, account["platform"])
    return {"success": True, "message": "账号已解绑"}


# ---------------------------------------------------------------------------
# Cookie 认证接口
# ---------------------------------------------------------------------------

@router.post("/connect-cookie/{platform}")
async def connect_cookie(platform: str, request: Request):
    """
    通过 Cookie 绑定平台账号（适用于小红书、微信视频号、微信公众号等无公开 OAuth API 的平台）。

    前端提交 JSON: {"cookie_value": "...", "nickname": "可选"}
    后端保存 Cookie 作为凭证并创建平台账号。
    """
    if platform not in COOKIE_AUTH_PLATFORMS:
        return JSONResponse(
            status_code=400,
            content={"success": False, "detail": f"平台 '{platform}' 不支持 Cookie 认证，请使用 OAuth"},
        )

    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("expected JSON object")
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"success": False, "detail": "无效的请求体"},
        )

    cookie_value = (body.get("cookie_value") or "").strip()
    if not cookie_value:
        return JSONResponse(
            status_code=400,
            content={"success": False, "detail": "Cookie 不能为空"},
        )

    nickname = (body.get("nickname") or "").strip() or f"{platform}_user"

    # 生成账号
    db = _get_db()
    platform_meta = _PLATFORM_LOOKUP.get(platform, {})
    platform_label = platform_meta.get("label", platform)

    account_id = str(uuid.uuid4())
    db.insert_platform_account({
        "id": account_id,
        "platform": platform,
        "auth_method": "cookie",
        "platform_uid": f"cookie_{account_id[:8]}",
        "username": nickname,
        "nickname": nickname,
        "avatar_url": "",
        "status": "active",
    })

    # 将 Cookie 保存为 OAuth credential（复用 access_token 字段存储 Cookie）
    db.upsert_oauth_credential(
        account_id=account_id,
        platform=platform,
        access_token=cookie_value,
        refresh_token="",
        expires_at=int(time.time()) + 86400 * 30,  # Cookie 默认 30 天有效期
        refresh_expires_at=None,
        raw="",
    )

    logger.info(
        "Cookie 绑定成功: platform=%s, nickname=%s, account_id=%s",
        platform, nickname, account_id,
    )
    return {
        "success": True,
        "message": f"{platform_label} 账号绑定成功",
        "account_id": account_id,
    }
