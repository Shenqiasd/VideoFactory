"""
OAuth 认证路由 — 多平台统一 OAuth 流程。

提供：
- GET  /oauth/platforms        已注册平台列表
- GET  /oauth/authorize/{platform}  发起 OAuth 授权
- GET  /oauth/callback/{platform}   处理 OAuth 回调
- GET  /oauth/accounts              已绑定账号列表
- GET  /oauth/accounts/{id}         单个账号详情
- DELETE /oauth/accounts/{id}       解绑账号
"""

import logging
import secrets
import uuid
from typing import Optional

from cachetools import TTLCache
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from api.auth import require_auth
from core.database import Database
from platform_services.registry import PlatformRegistry
from platform_services.exceptions import OAuthError, PlatformError

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])

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


def _create_oauth_state(platform: str) -> str:
    """创建一个带随机 nonce 的 OAuth state 并缓存。"""
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {"platform": platform}
    return state


def _validate_oauth_state(state: str) -> Optional[dict]:
    """验证并消费一个 OAuth state（一次性）。"""
    return _oauth_states.pop(state, None)


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.get("/platforms")
async def list_platforms():
    """列出所有已注册的平台。"""
    platforms = PlatformRegistry.list_platforms()
    return {"success": True, "data": platforms}


@router.get("/authorize/{platform}")
async def authorize(platform: str, request: Request):
    """
    发起 OAuth 授权。

    前端将用户重定向到此端点，后端生成平台 OAuth URL 并 302 重定向。
    """
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


@router.get("/callback/{platform}")
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
        if state:
            _validate_oauth_state(state)  # 消费 state，防止重放和内存泄漏
        from urllib.parse import quote
        return RedirectResponse(url=f"/platform-accounts?oauth=denied&reason={quote(reason, safe='')}", status_code=302)

    # 1. 验证 state
    state_data = _validate_oauth_state(state)
    if not state_data or state_data.get("platform") != platform:
        return JSONResponse(
            status_code=400,
            content={"success": False, "detail": "无效的 OAuth state，可能已过期"},
        )

    # 2. 获取平台服务
    service = PlatformRegistry.get(platform)
    if not service:
        return JSONResponse(
            status_code=404,
            content={"success": False, "detail": f"平台 '{platform}' 未注册"},
        )

    # 3. 用 code 换 token + 获取用户信息
    try:
        account_info, credential = await service.handle_callback(code=code, state=state)
    except PlatformError as e:
        logger.error("OAuth 回调失败: platform=%s, error=%s", platform, e)
        return JSONResponse(
            status_code=400,
            content={"success": False, "detail": f"授权失败: {e}"},
        )

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

    # 6. 重定向到前端账号管理页
    return RedirectResponse(url="/platform-accounts?oauth=success", status_code=302)


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
