"""
数据库引擎管理模块 - SQLAlchemy async
支持 PostgreSQL (asyncpg) 和 SQLite (aiosqlite) 后端
"""
import os
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    AsyncEngine,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ORM 基类
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


# ---------------------------------------------------------------------------
# 数据库 URL 解析
# ---------------------------------------------------------------------------


def get_database_url() -> str:
    """
    按优先级读取数据库连接 URL:
      1. Config().get("database", "url")
      2. 环境变量 DATABASE_URL
      3. 默认 SQLite: sqlite+aiosqlite:///data/video_factory.db
    """
    # 尝试从 Config 单例读取
    try:
        from core.config import Config
        url = Config().get("database", "url", default="")
        if url:
            return url
    except Exception:
        pass

    # 尝试环境变量
    url = os.environ.get("DATABASE_URL", "")
    if url:
        return url

    # 默认 SQLite
    return "sqlite+aiosqlite:///data/video_factory.db"


# ---------------------------------------------------------------------------
# 引擎 / 会话工厂
# ---------------------------------------------------------------------------


def get_engine() -> AsyncEngine:
    """获取或创建全局异步引擎"""
    global _engine
    if _engine is None:
        url = get_database_url()
        logger.info("初始化数据库引擎: %s", _mask_url(url))
        kwargs: dict = {}
        if url.startswith("sqlite"):
            # SQLite 不支持连接池配置
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs["pool_size"] = 5
            kwargs["max_overflow"] = 10
        _engine = create_async_engine(url, echo=False, **kwargs)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取或创建全局会话工厂"""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def init_db() -> None:
    """创建所有表（开发 / 测试用）"""
    from core.models import (  # noqa: F401 — 确保模型已注册
        AccountModel,
        PublishTaskModel,
        PublishJobModel,
        PublishJobEventModel,
    )

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表已创建")


async def close_db() -> None:
    """关闭引擎，释放连接池"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("数据库引擎已关闭")


# ---------------------------------------------------------------------------
# 同步引擎（供 Alembic 等同步工具使用）
# ---------------------------------------------------------------------------


def get_sync_engine():
    """为 Alembic 和其他同步工具提供同步引擎"""
    from sqlalchemy import create_engine

    url = get_database_url()
    sync_url = url.replace("+asyncpg", "+psycopg2").replace("+aiosqlite", "")
    return create_engine(sync_url)


def get_sync_database_url() -> str:
    """返回同步版本的数据库 URL"""
    url = get_database_url()
    return url.replace("+asyncpg", "+psycopg2").replace("+aiosqlite", "")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _mask_url(url: str) -> str:
    """遮蔽 URL 中的密码信息"""
    if "@" in url and "://" in url:
        prefix, rest = url.split("://", 1)
        if "@" in rest:
            creds, host = rest.rsplit("@", 1)
            if ":" in creds:
                user, _ = creds.split(":", 1)
                return f"{prefix}://{user}:***@{host}"
    return url


def reset_engine() -> None:
    """重置全局引擎（测试用）"""
    global _engine, _session_factory
    _engine = None
    _session_factory = None
