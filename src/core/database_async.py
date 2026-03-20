"""
异步数据库管理模块 - SQLAlchemy async
提供与 Database (database.py) 相同的公共接口，返回 dict 以保持向后兼容。
"""
import json
import logging
from datetime import datetime
from typing import Optional, List

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.db_engine import get_session_factory
from core.models import (
    AccountModel,
    PublishTaskModel,
    PublishJobModel,
    PublishJobEventModel,
)

logger = logging.getLogger(__name__)


class AsyncDatabase:
    """异步数据库管理器（SQLAlchemy）"""

    def __init__(self, session_factory: Optional[async_sessionmaker[AsyncSession]] = None):
        self._sf = session_factory or get_session_factory()

    # ------------------------------------------------------------------
    # accounts 方法
    # ------------------------------------------------------------------

    async def insert_account(self, account_data: dict) -> None:
        """插入账号"""
        async with self._sf() as session:
            model = AccountModel(
                id=account_data["id"],
                platform=account_data["platform"],
                name=account_data["name"],
                cookie_path=account_data.get("cookie_path", ""),
                status=account_data.get("status", "active"),
                last_test=account_data.get("last_test"),
                created_at=account_data.get("created_at", ""),
                is_default=int(account_data.get("is_default", False)),
                capabilities_json=json.dumps(
                    account_data.get("capabilities", {}), ensure_ascii=False
                ),
                last_error=account_data.get("last_error", ""),
            )
            session.add(model)
            await session.commit()

    async def get_accounts(self, platform: Optional[str] = None) -> List[dict]:
        """获取账号列表"""
        async with self._sf() as session:
            stmt = select(AccountModel)
            if platform:
                stmt = stmt.where(AccountModel.platform == platform)
            result = await session.execute(stmt)
            return [self._account_to_dict(row) for row in result.scalars().all()]

    async def get_account(self, account_id: str) -> Optional[dict]:
        """获取单个账号"""
        async with self._sf() as session:
            result = await session.execute(
                select(AccountModel).where(AccountModel.id == account_id)
            )
            row = result.scalar_one_or_none()
            return self._account_to_dict(row) if row else None

    async def delete_account(self, account_id: str) -> None:
        """删除账号"""
        async with self._sf() as session:
            await session.execute(
                delete(AccountModel).where(AccountModel.id == account_id)
            )
            await session.commit()

    async def set_default_account(self, account_id: str) -> bool:
        """设置默认账号"""
        account = await self.get_account(account_id)
        if not account:
            return False
        async with self._sf() as session:
            # 先取消同平台其他默认
            await session.execute(
                update(AccountModel)
                .where(AccountModel.platform == account["platform"])
                .values(is_default=0)
            )
            # 设为默认
            await session.execute(
                update(AccountModel)
                .where(AccountModel.id == account_id)
                .values(is_default=1)
            )
            await session.commit()
        return True

    async def update_account_validation(
        self,
        account_id: str,
        *,
        status: str,
        capabilities: dict,
        last_error: str = "",
        tested_at: Optional[datetime] = None,
    ) -> None:
        """更新账号验证结果"""
        when = (tested_at or datetime.now()).isoformat()
        async with self._sf() as session:
            await session.execute(
                update(AccountModel)
                .where(AccountModel.id == account_id)
                .values(
                    status=status,
                    capabilities_json=json.dumps(capabilities, ensure_ascii=False),
                    last_error=last_error,
                    last_test=when,
                )
            )
            await session.commit()

    async def update_account_test_time(self, account_id: str, test_time: datetime) -> None:
        """更新账号测试时间"""
        async with self._sf() as session:
            await session.execute(
                update(AccountModel)
                .where(AccountModel.id == account_id)
                .values(last_test=test_time.isoformat())
            )
            await session.commit()

    async def get_preferred_account(self, platform: str) -> Optional[dict]:
        """获取平台首选账号"""
        async with self._sf() as session:
            # SQLAlchemy 排序: is_default DESC, status='active' DESC, created_at DESC
            stmt = (
                select(AccountModel)
                .where(AccountModel.platform == platform)
                .order_by(
                    AccountModel.is_default.desc(),
                    (AccountModel.status == "active").desc(),
                    AccountModel.created_at.desc(),
                )
                .limit(1)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._account_to_dict(row) if row else None

    @staticmethod
    def _account_to_dict(model: AccountModel) -> dict:
        """AccountModel -> dict（与 Database._deserialize_account 保持一致）"""
        return {
            "id": model.id,
            "platform": model.platform,
            "name": model.name,
            "cookie_path": model.cookie_path,
            "status": model.status,
            "last_test": model.last_test,
            "created_at": model.created_at,
            "is_default": bool(model.is_default),
            "capabilities_json": model.capabilities_json,
            "capabilities": json.loads(model.capabilities_json or "{}"),
            "last_error": model.last_error,
        }

    # ------------------------------------------------------------------
    # publish_tasks 方法
    # ------------------------------------------------------------------

    async def insert_publish_task(self, task_data: dict) -> None:
        """插入发布任务"""
        async with self._sf() as session:
            model = PublishTaskModel(
                id=task_data["id"],
                task_id=task_data.get("task_id"),
                video_path=task_data["video_path"],
                platform=task_data["platform"],
                account_id=task_data["account_id"],
                title=task_data["title"],
                description=task_data.get("description"),
                tags=json.dumps(task_data.get("tags", [])),
                cover_path=task_data.get("cover_path"),
                publish_time=task_data.get("publish_time"),
                status=task_data["status"],
                publish_url=task_data.get("publish_url"),
                error=task_data.get("error"),
                created_at=task_data["created_at"],
                updated_at=task_data["updated_at"],
            )
            session.add(model)
            await session.commit()

    async def upsert_publish_task(self, task_data: dict) -> None:
        """插入或更新发布任务"""
        existing = await self.get_publish_task(task_data["id"])
        if existing:
            async with self._sf() as session:
                await session.execute(
                    update(PublishTaskModel)
                    .where(PublishTaskModel.id == task_data["id"])
                    .values(
                        task_id=task_data.get("task_id"),
                        video_path=task_data["video_path"],
                        platform=task_data["platform"],
                        account_id=task_data["account_id"],
                        title=task_data["title"],
                        description=task_data.get("description"),
                        tags=json.dumps(task_data.get("tags", [])),
                        cover_path=task_data.get("cover_path"),
                        publish_time=task_data.get("publish_time"),
                        status=task_data["status"],
                        publish_url=task_data.get("publish_url"),
                        error=task_data.get("error"),
                        updated_at=task_data.get("updated_at", datetime.now().isoformat()),
                    )
                )
                await session.commit()
        else:
            await self.insert_publish_task(task_data)

    async def get_publish_task(self, task_id: str) -> Optional[dict]:
        """获取单个发布任务"""
        async with self._sf() as session:
            result = await session.execute(
                select(PublishTaskModel).where(PublishTaskModel.id == task_id)
            )
            row = result.scalar_one_or_none()
            return self._publish_task_to_dict(row) if row else None

    async def get_publish_tasks(self, platform: Optional[str] = None) -> List[dict]:
        """获取发布任务列表"""
        async with self._sf() as session:
            stmt = select(PublishTaskModel).order_by(PublishTaskModel.created_at.desc())
            if platform:
                stmt = stmt.where(PublishTaskModel.platform == platform)
            result = await session.execute(stmt)
            return [self._publish_task_to_dict(row) for row in result.scalars().all()]

    async def update_task_status(self, task_id: str, status: str) -> None:
        """更新任务状态"""
        async with self._sf() as session:
            await session.execute(
                update(PublishTaskModel)
                .where(PublishTaskModel.id == task_id)
                .values(status=status, updated_at=datetime.now().isoformat())
            )
            await session.commit()

    async def update_task_result(
        self, task_id: str, status: str, publish_url: str = None, error: str = None
    ) -> None:
        """更新任务结果"""
        async with self._sf() as session:
            await session.execute(
                update(PublishTaskModel)
                .where(PublishTaskModel.id == task_id)
                .values(
                    status=status,
                    publish_url=publish_url,
                    error=error,
                    updated_at=datetime.now().isoformat(),
                )
            )
            await session.commit()

    async def delete_publish_task(self, task_id: str) -> None:
        """删除发布任务"""
        async with self._sf() as session:
            await session.execute(
                delete(PublishTaskModel).where(PublishTaskModel.id == task_id)
            )
            await session.commit()

    @staticmethod
    def _publish_task_to_dict(model: PublishTaskModel) -> dict:
        return {
            "id": model.id,
            "task_id": model.task_id,
            "video_path": model.video_path,
            "platform": model.platform,
            "account_id": model.account_id,
            "title": model.title,
            "description": model.description,
            "tags": model.tags,
            "cover_path": model.cover_path,
            "publish_time": model.publish_time,
            "status": model.status,
            "publish_url": model.publish_url,
            "error": model.error,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }

    # ------------------------------------------------------------------
    # publish_jobs 方法
    # ------------------------------------------------------------------

    async def upsert_publish_job(self, job: dict) -> None:
        """插入或更新单个发布作业"""
        now = datetime.now().isoformat()
        async with self._sf() as session:
            existing = await session.execute(
                select(PublishJobModel).where(PublishJobModel.job_id == job["job_id"])
            )
            if existing.scalar_one_or_none():
                await session.execute(
                    update(PublishJobModel)
                    .where(PublishJobModel.job_id == job["job_id"])
                    .values(
                        status=job["status"],
                        result_json=json.dumps(job.get("result", {}), ensure_ascii=False),
                        retry_count=job["retry_count"],
                        updated_at=now,
                    )
                )
            else:
                model = PublishJobModel(
                    job_id=job["job_id"],
                    task_id=job["task_id"],
                    platform=job["platform"],
                    scheduled_time=job["scheduled_time"],
                    product_json=json.dumps(job["product"], ensure_ascii=False),
                    metadata_json=json.dumps(job.get("metadata", {}), ensure_ascii=False),
                    product_type=job["product_type"],
                    product_identity=job["product_identity"],
                    idempotency_key=job["idempotency_key"],
                    status=job["status"],
                    result_json=json.dumps(job.get("result", {}), ensure_ascii=False),
                    retry_count=job["retry_count"],
                    max_retries=job["max_retries"],
                    created_at=job.get("created_at", now),
                    updated_at=now,
                )
                session.add(model)
            await session.commit()

    async def get_publish_jobs(
        self,
        task_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[dict]:
        """读取发布作业列表"""
        async with self._sf() as session:
            stmt = select(PublishJobModel).order_by(
                PublishJobModel.created_at.asc(),
                PublishJobModel.scheduled_time.asc(),
            )
            if task_id:
                stmt = stmt.where(PublishJobModel.task_id == task_id)
            if status:
                stmt = stmt.where(PublishJobModel.status == status)
            result = await session.execute(stmt)
            return [self._publish_job_to_dict(row) for row in result.scalars().all()]

    async def update_publish_job_status(
        self, job_id: str, status: str, result: dict = None
    ) -> None:
        """更新单个发布作业的状态和结果"""
        now = datetime.now().isoformat()
        async with self._sf() as session:
            await session.execute(
                update(PublishJobModel)
                .where(PublishJobModel.job_id == job_id)
                .values(
                    status=status,
                    result_json=json.dumps(result or {}, ensure_ascii=False),
                    updated_at=now,
                )
            )
            await session.commit()

    async def delete_publish_job(self, job_id: str) -> None:
        """删除单个发布作业"""
        async with self._sf() as session:
            await session.execute(
                delete(PublishJobModel).where(PublishJobModel.job_id == job_id)
            )
            await session.commit()

    @staticmethod
    def _publish_job_to_dict(model: PublishJobModel) -> dict:
        return {
            "job_id": model.job_id,
            "task_id": model.task_id,
            "platform": model.platform,
            "scheduled_time": model.scheduled_time,
            "product": json.loads(model.product_json or "{}"),
            "metadata": json.loads(model.metadata_json or "{}"),
            "product_type": model.product_type,
            "product_identity": model.product_identity,
            "idempotency_key": model.idempotency_key,
            "status": model.status,
            "result": json.loads(model.result_json or "{}"),
            "retry_count": model.retry_count,
            "max_retries": model.max_retries,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }

    # ------------------------------------------------------------------
    # publish_job_events 方法
    # ------------------------------------------------------------------

    async def record_publish_job_event(self, event_data: dict) -> None:
        """记录发布作业事件"""
        async with self._sf() as session:
            model = PublishJobEventModel(
                job_id=event_data["job_id"],
                task_id=event_data["task_id"],
                platform=event_data["platform"],
                event_type=event_data["event_type"],
                from_status=event_data.get("from_status", ""),
                to_status=event_data.get("to_status", ""),
                message=event_data.get("message", ""),
                payload_json=json.dumps(
                    event_data.get("payload", {}), ensure_ascii=False
                ),
                created_at=event_data.get(
                    "created_at", datetime.now().isoformat()
                ),
            )
            session.add(model)
            await session.commit()

    async def insert_publish_job_event(
        self,
        *,
        job_id: str,
        task_id: str,
        platform: str,
        event_type: str,
        from_status: str = "",
        to_status: str = "",
        message: str = "",
        payload: Optional[dict] = None,
        created_at: Optional[datetime] = None,
    ) -> None:
        """记录发布作业事件（关键字参数版，与 Database 接口一致）"""
        when = (created_at or datetime.now()).isoformat()
        await self.record_publish_job_event({
            "job_id": job_id,
            "task_id": task_id,
            "platform": platform,
            "event_type": event_type,
            "from_status": from_status,
            "to_status": to_status,
            "message": message,
            "payload": payload or {},
            "created_at": when,
        })

    async def get_publish_job_events(
        self,
        *,
        task_id: str = "",
        job_id: str = "",
        limit: int = 100,
    ) -> List[dict]:
        """获取发布作业事件列表"""
        async with self._sf() as session:
            stmt = select(PublishJobEventModel).order_by(
                PublishJobEventModel.id.desc()
            )
            if task_id:
                stmt = stmt.where(PublishJobEventModel.task_id == task_id)
            if job_id:
                stmt = stmt.where(PublishJobEventModel.job_id == job_id)
            stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return [self._event_to_dict(row) for row in result.scalars().all()]

    @staticmethod
    def _event_to_dict(model: PublishJobEventModel) -> dict:
        return {
            "id": model.id,
            "job_id": model.job_id,
            "task_id": model.task_id,
            "platform": model.platform,
            "event_type": model.event_type,
            "from_status": model.from_status,
            "to_status": model.to_status,
            "message": model.message,
            "payload_json": model.payload_json,
            "payload": json.loads(model.payload_json or "{}"),
            "created_at": model.created_at,
        }

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """关闭（兼容接口，实际引擎由 db_engine.close_db 管理）"""
        pass
