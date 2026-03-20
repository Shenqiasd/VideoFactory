"""
数据库管理模块 - SQLite
线程安全：使用 RLock 保护所有数据库操作
"""
import sqlite3
import json
import os
import threading
from pathlib import Path
from typing import Optional, List
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class Database:
    """SQLite 数据库管理器（线程安全）"""

    _VALID_TABLES = {"accounts", "publish_tasks", "publish_jobs", "publish_job_events"}

    def __init__(self, db_path: str = "data/video_factory.db"):
        resolved_path = os.environ.get("VF_DB_PATH", db_path)
        self.db_path = Path(resolved_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        """初始化数据库表"""
        with self._lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS publish_tasks (
                    id TEXT PRIMARY KEY,
                    task_id TEXT,
                    video_path TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    tags TEXT,
                    cover_path TEXT,
                    publish_time TEXT,
                    status TEXT NOT NULL,
                    publish_url TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                )
            """)

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    name TEXT NOT NULL,
                    cookie_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_test TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            self._ensure_column("accounts", "is_default", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("accounts", "capabilities_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column("accounts", "last_error", "TEXT NOT NULL DEFAULT ''")

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS publish_jobs (
                    job_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    scheduled_time REAL NOT NULL,
                    product_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    product_type TEXT NOT NULL,
                    product_identity TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    retry_count INTEGER NOT NULL,
                    max_retries INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_publish_jobs_task_id ON publish_jobs(task_id)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_publish_jobs_status ON publish_jobs(status)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_publish_jobs_idempotency_key ON publish_jobs(idempotency_key)"
            )
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS publish_job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    from_status TEXT NOT NULL,
                    to_status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_publish_job_events_task_id ON publish_job_events(task_id)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_publish_job_events_job_id ON publish_job_events(job_id)"
            )
            self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str):
        if table not in self._VALID_TABLES:
            raise ValueError(f"Invalid table name: {table}")
        # Note: called within _init_tables which already holds the lock
        columns = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    # ========== publish_tasks 方法 ==========

    def insert_publish_task(self, task_data: dict):
        """插入发布任务"""
        with self._lock:
            self.conn.execute("""
                INSERT INTO publish_tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task_data['id'],
                task_data.get('task_id'),
                task_data['video_path'],
                task_data['platform'],
                task_data['account_id'],
                task_data['title'],
                task_data.get('description'),
                json.dumps(task_data.get('tags', [])),
                task_data.get('cover_path'),
                task_data.get('publish_time'),
                task_data['status'],
                task_data.get('publish_url'),
                task_data.get('error'),
                task_data['created_at'],
                task_data['updated_at']
            ))
            self.conn.commit()

    def get_publish_tasks(self, platform: Optional[str] = None) -> List[dict]:
        """获取任务列表"""
        with self._lock:
            if platform:
                cursor = self.conn.execute(
                    "SELECT * FROM publish_tasks WHERE platform = ? ORDER BY created_at DESC",
                    (platform,)
                )
            else:
                cursor = self.conn.execute("SELECT * FROM publish_tasks ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def get_publish_task(self, task_id: str) -> Optional[dict]:
        """获取单个任务"""
        with self._lock:
            cursor = self.conn.execute("SELECT * FROM publish_tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_task_status(self, task_id: str, status: str):
        """更新任务状态"""
        with self._lock:
            self.conn.execute(
                "UPDATE publish_tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status, datetime.now().isoformat(), task_id)
            )
            self.conn.commit()

    def update_task_result(self, task_id: str, status: str, publish_url: str = None, error: str = None):
        """更新任务结果"""
        with self._lock:
            self.conn.execute(
                "UPDATE publish_tasks SET status = ?, publish_url = ?, error = ?, updated_at = ? WHERE id = ?",
                (status, publish_url, error, datetime.now().isoformat(), task_id)
            )
            self.conn.commit()

    def delete_publish_task(self, task_id: str):
        """删除任务"""
        with self._lock:
            self.conn.execute("DELETE FROM publish_tasks WHERE id = ?", (task_id,))
            self.conn.commit()

    # ========== publish_jobs 方法 ==========

    def replace_publish_jobs(self, jobs: List[dict]):
        """全量替换发布作业队列（已废弃，建议使用 upsert_publish_job）"""
        now = datetime.now().isoformat()
        rows = [
            (
                job["job_id"],
                job["task_id"],
                job["platform"],
                job["scheduled_time"],
                json.dumps(job["product"], ensure_ascii=False),
                json.dumps(job.get("metadata", {}), ensure_ascii=False),
                job["product_type"],
                job["product_identity"],
                job["idempotency_key"],
                job["status"],
                json.dumps(job.get("result", {}), ensure_ascii=False),
                job["retry_count"],
                job["max_retries"],
                job.get("created_at", now),
                job.get("updated_at", now),
            )
            for job in jobs
        ]

        with self._lock:
            with self.conn:
                self.conn.execute("DELETE FROM publish_jobs")
                self.conn.executemany(
                    """
                    INSERT INTO publish_jobs (
                        job_id, task_id, platform, scheduled_time, product_json, metadata_json,
                        product_type, product_identity, idempotency_key, status, result_json,
                        retry_count, max_retries, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )

    def upsert_publish_job(self, job: dict):
        """插入或更新单个发布作业"""
        now = datetime.now().isoformat()
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO publish_jobs (
                    job_id, task_id, platform, scheduled_time, product_json, metadata_json,
                    product_type, product_identity, idempotency_key, status, result_json,
                    retry_count, max_retries, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status = excluded.status,
                    result_json = excluded.result_json,
                    retry_count = excluded.retry_count,
                    updated_at = excluded.updated_at
                """,
                (
                    job["job_id"], job["task_id"], job["platform"],
                    job["scheduled_time"],
                    json.dumps(job["product"], ensure_ascii=False),
                    json.dumps(job.get("metadata", {}), ensure_ascii=False),
                    job["product_type"], job["product_identity"],
                    job["idempotency_key"], job["status"],
                    json.dumps(job.get("result", {}), ensure_ascii=False),
                    job["retry_count"], job["max_retries"],
                    job.get("created_at", now), now,
                ),
            )
            self.conn.commit()

    def delete_publish_job(self, job_id: str):
        """删除单个发布作业"""
        with self._lock:
            self.conn.execute("DELETE FROM publish_jobs WHERE job_id = ?", (job_id,))
            self.conn.commit()

    def update_publish_job_status(self, job_id: str, status: str, result: dict = None):
        """更新单个发布作业的状态和结果"""
        now = datetime.now().isoformat()
        with self._lock:
            self.conn.execute(
                "UPDATE publish_jobs SET status = ?, result_json = ?, updated_at = ? WHERE job_id = ?",
                (status, json.dumps(result or {}, ensure_ascii=False), now, job_id),
            )
            self.conn.commit()

    def get_publish_jobs(self) -> List[dict]:
        """读取所有发布作业"""
        with self._lock:
            cursor = self.conn.execute(
                "SELECT * FROM publish_jobs ORDER BY created_at ASC, scheduled_time ASC, rowid ASC"
            )
            jobs: List[dict] = []
            for row in cursor.fetchall():
                item = dict(row)
                item["product"] = json.loads(item.pop("product_json") or "{}")
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
                item["result"] = json.loads(item.pop("result_json") or "{}")
                jobs.append(item)
            return jobs

    # ========== accounts 方法 ==========

    def insert_account(self, account_data: dict):
        """插入账号"""
        with self._lock:
            self.conn.execute("""
                INSERT INTO accounts (
                    id, platform, name, cookie_path, status, last_test, created_at,
                    is_default, capabilities_json, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                account_data['id'],
                account_data['platform'],
                account_data['name'],
                account_data['cookie_path'],
                account_data['status'],
                account_data.get('last_test'),
                account_data['created_at'],
                int(account_data.get('is_default', False)),
                json.dumps(account_data.get('capabilities', {}), ensure_ascii=False),
                account_data.get('last_error', ''),
            ))
            self.conn.commit()

    def get_accounts(self, platform: Optional[str] = None) -> List[dict]:
        """获取账号列表"""
        with self._lock:
            if platform:
                cursor = self.conn.execute(
                    "SELECT * FROM accounts WHERE platform = ?", (platform,)
                )
            else:
                cursor = self.conn.execute("SELECT * FROM accounts")
            return [self._deserialize_account(dict(row)) for row in cursor.fetchall()]

    def get_account(self, account_id: str) -> Optional[dict]:
        """获取单个账号"""
        with self._lock:
            cursor = self.conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
            row = cursor.fetchone()
            return self._deserialize_account(dict(row)) if row else None

    @staticmethod
    def _deserialize_account(row: dict) -> dict:
        row["is_default"] = bool(row.get("is_default", 0))
        row["capabilities"] = json.loads(row.get("capabilities_json") or "{}")
        return row

    def update_account_test_time(self, account_id: str, test_time: datetime):
        """更新账号测试时间"""
        with self._lock:
            self.conn.execute(
                "UPDATE accounts SET last_test = ? WHERE id = ?",
                (test_time.isoformat(), account_id)
            )
            self.conn.commit()

    def update_account_validation(
        self,
        account_id: str,
        *,
        status: str,
        capabilities: dict,
        last_error: str = "",
        tested_at: Optional[datetime] = None,
    ):
        when = (tested_at or datetime.now()).isoformat()
        with self._lock:
            self.conn.execute(
                """
                UPDATE accounts
                SET status = ?, capabilities_json = ?, last_error = ?, last_test = ?
                WHERE id = ?
                """,
                (status, json.dumps(capabilities, ensure_ascii=False), last_error, when, account_id),
            )
            self.conn.commit()

    def set_default_account(self, account_id: str) -> bool:
        account = self.get_account(account_id)
        if not account:
            return False
        with self._lock:
            with self.conn:
                self.conn.execute(
                    "UPDATE accounts SET is_default = 0 WHERE platform = ?",
                    (account["platform"],),
                )
                self.conn.execute(
                    "UPDATE accounts SET is_default = 1 WHERE id = ?",
                    (account_id,),
                )
        return True

    def get_preferred_account(self, platform: str) -> Optional[dict]:
        with self._lock:
            cursor = self.conn.execute(
                """
                SELECT * FROM accounts
                WHERE platform = ?
                ORDER BY is_default DESC, status = 'active' DESC, created_at DESC
                LIMIT 1
                """,
                (platform,),
            )
            row = cursor.fetchone()
            return self._deserialize_account(dict(row)) if row else None

    def delete_account(self, account_id: str):
        """删除账号"""
        with self._lock:
            self.conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            self.conn.commit()

    # ========== publish_job_events 方法 ==========

    def insert_publish_job_event(
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
    ):
        when = (created_at or datetime.now()).isoformat()
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO publish_job_events (
                    job_id, task_id, platform, event_type, from_status, to_status,
                    message, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    task_id,
                    platform,
                    event_type,
                    from_status,
                    to_status,
                    message,
                    json.dumps(payload or {}, ensure_ascii=False),
                    when,
                ),
            )
            self.conn.commit()

    def get_publish_job_events(
        self,
        *,
        task_id: str = "",
        job_id: str = "",
        limit: int = 100,
    ) -> List[dict]:
        with self._lock:
            clauses = []
            params: List[object] = []
            if task_id:
                clauses.append("task_id = ?")
                params.append(task_id)
            if job_id:
                clauses.append("job_id = ?")
                params.append(job_id)

            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            params.append(limit)
            cursor = self.conn.execute(
                f"""
                SELECT * FROM publish_job_events
                {where}
                ORDER BY id DESC
                LIMIT ?
                """,
                tuple(params),
            )
            rows = []
            for row in cursor.fetchall():
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json") or "{}")
                rows.append(item)
            return rows

    def close(self):
        """关闭数据库连接"""
        with self._lock:
            self.conn.close()
