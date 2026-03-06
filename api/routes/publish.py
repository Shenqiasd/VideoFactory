"""发布账号管理路由"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from distribute.models import Account
from distribute.account_store import AccountStore

router = APIRouter()

class AccountCreate(BaseModel):
    platform: str
    account_id: str
    account_name: str = ""
    cookies_path: str = ""
    enabled: bool = True

_store = None

def get_store() -> AccountStore:
    global _store
    if _store is None:
        _store = AccountStore()
    return _store

@router.post("/accounts")
async def create_account(req: AccountCreate):
    """创建账号"""
    store = get_store()
    account = Account(
        platform=req.platform,
        account_id=req.account_id,
        account_name=req.account_name,
        cookies_path=req.cookies_path,
        enabled=req.enabled
    )
    store.add(account)
    return {"message": "账号已创建", "account_id": req.account_id}

@router.get("/accounts")
async def list_accounts():
    """列出所有账号"""
    store = get_store()
    accounts = store.list_all()
    return {"accounts": [
        {
            "platform": a.platform,
            "account_id": a.account_id,
            "account_name": a.account_name,
            "enabled": a.enabled
        } for a in accounts
    ]}

@router.delete("/accounts/{platform}/{account_id}")
async def delete_account(platform: str, account_id: str):
    """删除账号"""
    store = get_store()
    if not store.delete(platform, account_id):
        raise HTTPException(status_code=404, detail="账号不存在")
    return {"message": "账号已删除"}
