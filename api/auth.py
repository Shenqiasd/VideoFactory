"""
认证模块 — 基于用户名/密码的完整登录系统。

用户数据存储在 config/users.json（bcrypt 哈希密码）。
会话通过 itsdangerous 签名的 httpOnly Cookie 管理。

当没有任何注册用户时，认证完全跳过（向后兼容本地开发）。
"""
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.context import CryptContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Password hashing (bcrypt)
# ---------------------------------------------------------------------------

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)


# ---------------------------------------------------------------------------
# User storage (JSON file)
# ---------------------------------------------------------------------------

def _users_file_path() -> Path:
    """Return the path to the users JSON file."""
    env_path = os.environ.get("VF_USERS_FILE", "").strip()
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().parents[1] / "config" / "users.json"


def _read_users() -> list[dict]:
    """Read all users from the JSON file."""
    path = _users_file_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


def _write_users(users: list[dict]) -> None:
    """Write all users to the JSON file."""
    path = _users_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(users, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_user_by_username(username: str) -> Optional[dict]:
    """Find a user by username (case-insensitive)."""
    normalized = username.strip().lower()
    for user in _read_users():
        if user.get("username", "").strip().lower() == normalized:
            return user
    return None


def create_user(username: str, password: str) -> dict:
    """Create a new user. Raises ValueError if username already taken."""
    if get_user_by_username(username):
        raise ValueError("用户名已存在")

    users = _read_users()
    user = {
        "username": username.strip(),
        "password_hash": hash_password(password),
        "created_at": time.time(),
    }
    users.append(user)
    _write_users(users)
    return user


def user_count() -> int:
    """Return the total number of registered users."""
    return len(_read_users())


# ---------------------------------------------------------------------------
# Session management (signed cookies via itsdangerous)
# ---------------------------------------------------------------------------

_COOKIE_NAME = "vf_session"
_SESSION_MAX_AGE = 86400 * 30  # 30 days


def _get_secret_key() -> str:
    """
    Return the signing secret for session cookies.
    Uses VF_SECRET_KEY env var. If not set, generates and persists one.
    """
    env_key = os.environ.get("VF_SECRET_KEY", "").strip()
    if env_key:
        return env_key

    # Auto-generate and persist to a file so sessions survive restarts
    key_path = Path(__file__).resolve().parents[1] / "config" / ".session_secret"
    if key_path.exists():
        stored = key_path.read_text(encoding="utf-8").strip()
        if stored:
            return stored

    generated = secrets.token_hex(32)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(generated, encoding="utf-8")
    return generated


def _get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_get_secret_key(), salt="vf-session")


def create_session_token(username: str) -> str:
    """Create a signed session token for the given username."""
    serializer = _get_serializer()
    return serializer.dumps({"u": username})


def verify_session_token(token: str) -> Optional[str]:
    """
    Verify a session token and return the username, or None if invalid.
    """
    serializer = _get_serializer()
    try:
        data = serializer.loads(token, max_age=_SESSION_MAX_AGE)
        username = data.get("u")
        if username and get_user_by_username(username):
            return username
        return None
    except (BadSignature, SignatureExpired):
        return None


# ---------------------------------------------------------------------------
# Auth state helpers
# ---------------------------------------------------------------------------

def auth_enabled() -> bool:
    """Auth is enabled when at least one user has been registered."""
    return user_count() > 0


def registration_allowed() -> bool:
    """
    Registration is allowed when:
    - No users exist (bootstrap first user), OR
    - VF_ALLOW_REGISTRATION env var is set to 'true'
    """
    if user_count() == 0:
        return True
    return os.environ.get("VF_ALLOW_REGISTRATION", "").strip().lower() == "true"


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

def _extract_session(request: Request) -> Optional[str]:
    """Extract session token from Cookie."""
    return request.cookies.get(_COOKIE_NAME)


async def require_auth(request: Request) -> None:
    """
    FastAPI dependency — protect API endpoints.

    * No users registered → 401 (must register first)
    * Valid session cookie → allow
    * Otherwise → 401
    """
    if not auth_enabled():
        raise HTTPException(status_code=401, detail="请先注册账户")

    token = _extract_session(request)
    if token and verify_session_token(token):
        return

    raise HTTPException(status_code=401, detail="未授权：请先登录")


class _AuthRedirect(Exception):
    """Raised by require_auth_page to trigger a redirect."""

    def __init__(self, location: str) -> None:
        self.location = location


async def require_auth_page(request: Request):
    """
    Page-level auth dependency — redirect to /login or /register.

    * No users registered → 302 /register
    * Valid session cookie → allow
    * Otherwise → 302 /login?next=<current_path>
    """
    if not auth_enabled():
        raise _AuthRedirect("/register")

    token = _extract_session(request)
    if token and verify_session_token(token):
        return

    from urllib.parse import quote
    next_url = quote(str(request.url.path), safe="")
    raise _AuthRedirect(f"/login?next={next_url}")


# ---------------------------------------------------------------------------
# Token/secret masking utilities
# ---------------------------------------------------------------------------

def mask_secret(value: str, *, visible_tail: int = 4) -> str:
    """
    Mask a sensitive string, showing only the last visible_tail characters.
    Empty or short strings are returned as-is.
    """
    if not value or len(value) <= visible_tail:
        return value
    return "*" * 4 + value[-visible_tail:]


def mask_dict_secrets(
    data: dict,
    sensitive_keys: frozenset[str] = frozenset(
        {"api_key", "token", "access_token", "secret", "password"}
    ),
) -> dict:
    """
    Recursively traverse a dict, masking string values whose keys
    match sensitive_keys.
    """
    masked: dict = {}
    for key, value in data.items():
        if isinstance(value, dict):
            masked[key] = mask_dict_secrets(value, sensitive_keys)
        elif isinstance(value, str) and key in sensitive_keys:
            masked[key] = mask_secret(value)
        else:
            masked[key] = value
    return masked
