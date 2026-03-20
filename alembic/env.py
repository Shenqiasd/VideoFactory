"""
Alembic 迁移环境配置
从 core.db_engine 读取数据库 URL，导入 ORM 模型的 metadata。
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from core.db_engine import Base, get_sync_database_url

# 确保所有模型已导入，Alembic 才能检测到
from core.models import (  # noqa: F401
    AccountModel,
    PublishTaskModel,
    PublishJobModel,
    PublishJobEventModel,
)

# Alembic Config 对象
config = context.config

# 设置日志
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 目标 metadata（用于 autogenerate）
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """以 'offline' 模式运行迁移 —— 只生成 SQL 脚本。"""
    url = get_sync_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """以 'online' 模式运行迁移 —— 连接数据库执行。"""
    # 动态设置 URL
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = get_sync_database_url()

    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
