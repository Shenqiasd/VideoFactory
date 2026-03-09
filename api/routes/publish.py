"""发布账号管理路由"""
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import uuid

from core.database import Database
from distribute.publisher import PLATFORM_SCRIPT_MAP

router = APIRouter()

class AccountCreate(BaseModel):
    platform: str
    name: str
    cookie_path: str = ""
    is_default: bool = False

_db: Optional[Database] = None

def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


def detect_account_capabilities(platform: str, cookie_path: str) -> dict:
    cookie_exists = bool(cookie_path) and os.path.exists(cookie_path)
    platform_supported = platform in PLATFORM_SCRIPT_MAP
    return {
        "platform_supported": platform_supported,
        "cookie_required": True,
        "cookie_exists": cookie_exists,
        "can_auto_publish": platform_supported and cookie_exists,
        "can_manual_publish": cookie_exists,
    }


def validate_account(platform: str, cookie_path: str) -> tuple[str, dict, str]:
    capabilities = detect_account_capabilities(platform, cookie_path)
    if not capabilities["platform_supported"]:
        return "invalid", capabilities, f"平台暂不支持自动发布: {platform}"
    if not capabilities["cookie_exists"]:
        return "invalid", capabilities, "Cookie 文件不存在或未配置"
    return "active", capabilities, ""

@router.post("/accounts")
async def create_account(req: AccountCreate):
    """创建账号"""
    db = get_db()
    account_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    status, capabilities, last_error = validate_account(req.platform, req.cookie_path)

    account_data = {
        "id": account_id,
        "platform": req.platform,
        "name": req.name,
        "cookie_path": req.cookie_path,
        "status": status,
        "last_test": now,
        "created_at": now,
        "is_default": req.is_default,
        "capabilities": capabilities,
        "last_error": last_error,
    }

    db.insert_account(account_data)
    existing = db.get_accounts(req.platform)
    if req.is_default or len(existing) == 1:
        db.set_default_account(account_id)
    return {"message": "账号已创建", "account_id": account_id}

@router.get("/accounts")
async def list_accounts(platform: Optional[str] = None):
    """列出所有账号"""
    db = get_db()
    accounts = db.get_accounts(platform)
    return {"accounts": accounts}

@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str):
    """删除账号"""
    db = get_db()
    account = db.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    db.delete_account(account_id)
    return {"message": "账号已删除"}


@router.post("/accounts/{account_id}/test")
async def test_account(account_id: str):
    """测试账号配置是否可用"""
    db = get_db()
    account = db.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")

    status, capabilities, last_error = validate_account(account["platform"], account.get("cookie_path", ""))
    db.update_account_validation(
        account_id,
        status=status,
        capabilities=capabilities,
        last_error=last_error,
        tested_at=datetime.now(),
    )
    refreshed = db.get_account(account_id)

    return {
        "success": status == "active",
        "account_id": account_id,
        "platform": account["platform"],
        "cookie_exists": capabilities["cookie_exists"],
        "capabilities": capabilities,
        "status": refreshed["status"] if refreshed else status,
        "last_error": refreshed["last_error"] if refreshed else last_error,
        "tested_at": datetime.now().isoformat(),
    }


@router.post("/accounts/{account_id}/default")
async def set_default_account(account_id: str):
    db = get_db()
    if not db.set_default_account(account_id):
        raise HTTPException(status_code=404, detail="账号不存在")
    account = db.get_account(account_id)
    return {
        "message": "默认账号已更新",
        "account_id": account_id,
        "platform": account["platform"] if account else "",
    }
