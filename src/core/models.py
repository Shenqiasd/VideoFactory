"""
SQLAlchemy ORM 模型
对应 database.py 中 _init_tables() 定义的 4 张表
"""
from sqlalchemy import (
    Column,
    String,
    Text,
    Integer,
    Float,
    ForeignKey,
    Index,
)

from core.db_engine import Base


# ---------------------------------------------------------------------------
# accounts 表
# ---------------------------------------------------------------------------


class AccountModel(Base):
    """账号表"""

    __tablename__ = "accounts"

    id = Column(String, primary_key=True)
    platform = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    cookie_path = Column(String, nullable=False, default="")
    status = Column(String, nullable=False, default="active")
    last_test = Column(String, nullable=True)
    created_at = Column(String, nullable=False, default="")
    # 通过 _ensure_column 添加的列
    is_default = Column(Integer, nullable=False, default=0)
    capabilities_json = Column(Text, nullable=False, default="{}")
    last_error = Column(String, nullable=False, default="")


# ---------------------------------------------------------------------------
# publish_tasks 表
# ---------------------------------------------------------------------------


class PublishTaskModel(Base):
    """发布任务表"""

    __tablename__ = "publish_tasks"

    id = Column(String, primary_key=True)
    task_id = Column(String, nullable=True)
    video_path = Column(String, nullable=False)
    platform = Column(String, nullable=False)
    account_id = Column(String, ForeignKey("accounts.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    tags = Column(Text, nullable=True)
    cover_path = Column(String, nullable=True)
    publish_time = Column(String, nullable=True)
    status = Column(String, nullable=False)
    publish_url = Column(String, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)


# ---------------------------------------------------------------------------
# publish_jobs 表
# ---------------------------------------------------------------------------


class PublishJobModel(Base):
    """发布作业表"""

    __tablename__ = "publish_jobs"

    job_id = Column(String, primary_key=True)
    task_id = Column(String, nullable=False, index=True)
    platform = Column(String, nullable=False)
    scheduled_time = Column(Float, nullable=False)
    product_json = Column(Text, nullable=False)
    metadata_json = Column(Text, nullable=False)
    product_type = Column(String, nullable=False)
    product_identity = Column(String, nullable=False)
    idempotency_key = Column(String, nullable=False)
    status = Column(String, nullable=False, index=True)
    result_json = Column(Text, nullable=False)
    retry_count = Column(Integer, nullable=False)
    max_retries = Column(Integer, nullable=False)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    __table_args__ = (
        Index("idx_publish_jobs_idempotency_key", "idempotency_key"),
    )


# ---------------------------------------------------------------------------
# publish_job_events 表
# ---------------------------------------------------------------------------


class PublishJobEventModel(Base):
    """发布作业事件表"""

    __tablename__ = "publish_job_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String, nullable=False, index=True)
    task_id = Column(String, nullable=False, index=True)
    platform = Column(String, nullable=False)
    event_type = Column(String, nullable=False)
    from_status = Column(String, nullable=False)
    to_status = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    payload_json = Column(Text, nullable=False)
    created_at = Column(String, nullable=False)
