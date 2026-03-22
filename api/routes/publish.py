"""发布账号管理路由"""
import os
import json
import shutil
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi import File, Form, UploadFile

from api.auth import require_auth
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import uuid

from core.database import Database
from core.config import Config
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


def get_account_storage_dir() -> Path:
    config = Config()
    base = config.get(
        "distribute",
        "account_dir",
        default=str(Path.home() / ".video-factory" / "accounts"),
    )
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_filename(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in name).strip("_") or "account"


PLATFORM_COOKIE_RULES = {
    "douyin": {
        "domains": ["douyin.com", "iesdouyin.com", "tiktok.com"],
        "cookie_names": ["sessionid", "sessionid_ss", "ttwid"],
    },
    "xiaohongshu": {
        "domains": ["xiaohongshu.com"],
        "cookie_names": ["a1", "web_session", "webId"],
    },
    "bilibili": {
        "domains": ["bilibili.com"],
        "cookie_names": ["SESSDATA", "bili_jct", "DedeUserID"],
    },
    "youtube": {
        "domains": ["youtube.com", "google.com"],
        "cookie_names": ["SID", "HSID", "SAPISID", "__Secure-1PSID", "LOGIN_INFO"],
    },
    "weixin": {
        "domains": ["weixin.qq.com", "channels.weixin.qq.com"],
        "cookie_names": ["wap_sid2", "wxuin", "pass_ticket"],
    },
}


def _detect_cookie_format(cookie_path: str) -> tuple[bool, str, str, dict]:
    if not cookie_path or not os.path.exists(cookie_path):
        return False, "missing", "Cookie 文件不存在", {}

    path = Path(cookie_path)
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        return False, "unreadable", f"Cookie 文件不可读: {exc}", {}

    text = raw.strip()
    ext = path.suffix.lower()

    if ext == ".json":
        try:
            payload = json.loads(text or "{}")
        except json.JSONDecodeError as exc:
            return False, "json", f"Cookie JSON 格式错误: {exc.msg}", {}
        if not isinstance(payload, (dict, list)):
            return False, "json", "Cookie JSON 必须是对象或数组", {}
        return True, "json", "", {"payload": payload, "raw": raw}

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if lines and (lines[0].startswith("# Netscape HTTP Cookie File") or "\t" in lines[0]):
        return True, "netscape", "", {"payload": None, "raw": raw}

    if text.startswith("{") or text.startswith("["):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            return False, "json", f"Cookie JSON 格式错误: {exc.msg}", {}
        if not isinstance(payload, (dict, list)):
            return False, "json", "Cookie JSON 必须是对象或数组", {}
        return True, "json", "", {"payload": payload, "raw": raw}

    return False, "unknown", "暂不识别的 Cookie 文件格式，请上传 JSON 或 Netscape cookies.txt", {}


def _extract_cookie_signatures(format_kind: str, payload: object, raw: str) -> tuple[set[str], set[str]]:
    domains: set[str] = set()
    cookie_names: set[str] = set()

    if format_kind == "netscape":
        for line in raw.splitlines():
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            parts = text.split("\t")
            if len(parts) >= 7:
                domains.add(parts[0].lstrip(".").lower())
                cookie_names.add(parts[5])
        return domains, cookie_names

    items = []
    if isinstance(payload, dict):
        if isinstance(payload.get("cookies"), list):
            items = payload["cookies"]
        elif isinstance(payload.get("origins"), list):
            items = payload.get("cookies", [])
        else:
            items = [payload]
    elif isinstance(payload, list):
        items = payload

    for item in items:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain", "")).lstrip(".").lower()
        name = str(item.get("name", ""))
        if domain:
            domains.add(domain)
        if name:
            cookie_names.add(name)

    return domains, cookie_names


def _validate_cookie_for_platform(platform: str, format_kind: str, payload: object, raw: str) -> tuple[bool, str]:
    rules = PLATFORM_COOKIE_RULES.get(platform)
    if not rules:
        return True, ""

    domains, cookie_names = _extract_cookie_signatures(format_kind, payload, raw)
    if not domains and not cookie_names:
        # 空白/脱敏 cookie 无法可靠识别平台时，保留可用状态，避免把人工录入流程直接卡死。
        return True, ""
    domain_matched = any(any(rule in domain for rule in rules["domains"]) for domain in domains)
    name_matched = any(name in cookie_names for name in rules["cookie_names"])

    if domain_matched or name_matched:
        return True, ""

    hint = "、".join(rules["domains"])
    return False, f"Cookie 内容与平台不匹配，未识别到 {platform} 相关域名/关键字段（期望域名如：{hint}）"


def detect_account_capabilities(platform: str, cookie_path: str) -> dict:
    cookie_exists = bool(cookie_path) and os.path.exists(cookie_path)
    platform_supported = platform in PLATFORM_SCRIPT_MAP
    format_valid, format_kind, format_error, details = _detect_cookie_format(cookie_path) if cookie_exists else (False, "missing", "Cookie 文件不存在", {})
    platform_cookie_match, platform_cookie_error = (
        _validate_cookie_for_platform(platform, format_kind, details.get("payload"), details.get("raw", ""))
        if cookie_exists and format_valid
        else (False, "")
    )
    return {
        "platform_supported": platform_supported,
        "cookie_required": True,
        "cookie_exists": cookie_exists,
        "format_valid": format_valid,
        "format_kind": format_kind,
        "format_error": format_error,
        "platform_cookie_match": platform_cookie_match,
        "platform_cookie_error": platform_cookie_error,
        "can_auto_publish": platform_supported and cookie_exists and format_valid and platform_cookie_match,
        "can_manual_publish": cookie_exists and format_valid and platform_cookie_match,
    }


def validate_account(platform: str, cookie_path: str) -> tuple[str, dict, str]:
    capabilities = detect_account_capabilities(platform, cookie_path)
    if not capabilities["platform_supported"]:
        return "invalid", capabilities, f"平台暂不支持自动发布: {platform}"
    if not capabilities["cookie_exists"]:
        return "invalid", capabilities, "Cookie 文件不存在或未配置"
    if not capabilities["format_valid"]:
        return "invalid", capabilities, capabilities.get("format_error", "Cookie 文件格式不合法")
    if not capabilities["platform_cookie_match"]:
        return "invalid", capabilities, capabilities.get("platform_cookie_error", "Cookie 内容与平台不匹配")
    return "active", capabilities, ""


async def _persist_uploaded_cookie(
    *,
    platform: str,
    account_name: str,
    upload: UploadFile,
) -> str:
    storage_dir = get_account_storage_dir() / platform
    storage_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.filename or "").suffix or ".json"
    target = storage_dir / f"{_safe_filename(account_name)}_{uuid.uuid4().hex[:8]}{suffix}"
    with target.open("wb") as fh:
        shutil.copyfileobj(upload.file, fh)
    return str(target)


def _build_account_payload(account: dict) -> dict:
    item = dict(account)
    cookie_path = item.get("cookie_path", "")
    item["cookie_filename"] = Path(cookie_path).name if cookie_path else ""
    return item

@router.post("/accounts", dependencies=[Depends(require_auth)])
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


@router.post("/accounts/upload", dependencies=[Depends(require_auth)])
async def create_account_with_upload(
    platform: str = Form(...),
    name: str = Form(...),
    is_default: bool = Form(False),
    cookie_path: str = Form(""),
    cookie_file: Optional[UploadFile] = File(None),
):
    if cookie_file is not None and cookie_file.filename:
        cookie_path = await _persist_uploaded_cookie(platform=platform, account_name=name, upload=cookie_file)

    req = AccountCreate(
        platform=platform,
        name=name,
        cookie_path=cookie_path,
        is_default=is_default,
    )
    result = await create_account(req)
    account = get_db().get_account(result["account_id"])
    return {
        **result,
        "cookie_path": account["cookie_path"] if account else cookie_path,
        "storage_dir": str(get_account_storage_dir() / platform),
    }

@router.get("/accounts")
async def list_accounts(platform: Optional[str] = None):
    """列出所有账号"""
    db = get_db()
    accounts = db.get_accounts(platform)
    return {"accounts": [_build_account_payload(account) for account in accounts]}


@router.get("/accounts/config")
async def get_account_config():
    storage_dir = get_account_storage_dir()
    return {
        "storage_dir": str(storage_dir),
        "supported_platforms": sorted(PLATFORM_SCRIPT_MAP.keys()),
        "accepted_formats": ["json", "netscape_cookies_txt"],
    }


@router.post("/accounts/{account_id}/cookie", dependencies=[Depends(require_auth)])
async def replace_account_cookie(
    account_id: str,
    cookie_file: UploadFile = File(...),
):
    db = get_db()
    account = db.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")

    old_path = Path(account.get("cookie_path", ""))
    new_path = await _persist_uploaded_cookie(
        platform=account["platform"],
        account_name=account["name"],
        upload=cookie_file,
    )
    status, capabilities, last_error = validate_account(account["platform"], new_path)
    now = datetime.now().isoformat()
    db.conn.execute(
        """
        UPDATE accounts
        SET cookie_path = ?, status = ?, capabilities_json = ?, last_error = ?, last_test = ?
        WHERE id = ?
        """,
        (
            new_path,
            status,
            json.dumps(capabilities, ensure_ascii=False),
            last_error,
            now,
            account_id,
        ),
    )
    db.conn.commit()

    storage_dir = get_account_storage_dir()
    if old_path.exists():
        try:
            old_path.relative_to(storage_dir)
            if old_path != Path(new_path):
                old_path.unlink(missing_ok=True)
        except Exception:
            pass

    refreshed = db.get_account(account_id)
    return {
        "message": "Cookie 已更新",
        "account": _build_account_payload(refreshed) if refreshed else None,
    }

@router.delete("/accounts/{account_id}", dependencies=[Depends(require_auth)])
async def delete_account(account_id: str):
    """删除账号"""
    db = get_db()
    account = db.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    cookie_path = Path(account.get("cookie_path", ""))
    storage_dir = get_account_storage_dir()
    db.delete_account(account_id)
    if cookie_path.exists():
        try:
            cookie_path.relative_to(storage_dir)
            cookie_path.unlink(missing_ok=True)
        except Exception:
            pass
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
