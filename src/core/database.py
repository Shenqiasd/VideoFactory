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

    _VALID_TABLES = {
        "accounts", "publish_tasks", "publish_jobs", "publish_job_events",
        "platform_accounts", "oauth_credentials", "publish_tasks_v2",
    }

    def __init__(self, db_path: str = "data/video_factory.db"):
        resolved_path = os.environ.get("VF_DB_PATH", db_path)
        self.db_path = Path(resolved_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("PRAGMA foreign_keys = ON")
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

            # ---- Sprint 1: 多平台认证发布 ----

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS platform_accounts (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL,
                    auth_method TEXT NOT NULL DEFAULT 'oauth2',
                    platform_uid TEXT NOT NULL DEFAULT '',
                    username TEXT DEFAULT '',
                    nickname TEXT NOT NULL,
                    avatar_url TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    cookie_path TEXT DEFAULT '',
                    last_login_at TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(platform, platform_uid)
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_platform_accounts_platform "
                "ON platform_accounts(platform)"
            )

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS oauth_credentials (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT NOT NULL DEFAULT '',
                    expires_at INTEGER NOT NULL,
                    refresh_expires_at INTEGER,
                    raw TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(account_id, platform),
                    FOREIGN KEY (account_id) REFERENCES platform_accounts(id) ON DELETE CASCADE
                )
            """)

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS publish_tasks_v2 (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL DEFAULT '',
                    account_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    tags TEXT DEFAULT '[]',
                    video_path TEXT DEFAULT '',
                    cover_path TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    scheduled_at TEXT,
                    attempts INTEGER DEFAULT 0,
                    max_attempts INTEGER DEFAULT 3,
                    error_message TEXT DEFAULT '',
                    post_id TEXT DEFAULT '',
                    permalink TEXT DEFAULT '',
                    platform_options TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    published_at TEXT,
                    FOREIGN KEY (account_id) REFERENCES platform_accounts(id) ON DELETE CASCADE
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_publish_tasks_v2_status "
                "ON publish_tasks_v2(status)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_publish_tasks_v2_account "
                "ON publish_tasks_v2(account_id)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_publish_tasks_v2_scheduled "
                "ON publish_tasks_v2(scheduled_at)"
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
        """删除账号（同时清理关联的 publish_tasks）"""
        with self._lock:
            self.conn.execute("DELETE FROM publish_tasks WHERE account_id = ?", (account_id,))
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

    # ========== platform_accounts 方法 (Sprint 1) ==========

    def insert_platform_account(self, account: dict) -> None:
        """插入平台账号"""
        with self._lock:
            self.conn.execute("""
                INSERT INTO platform_accounts (
                    id, user_id, platform, auth_method, platform_uid,
                    username, nickname, avatar_url, status, cookie_path,
                    last_login_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                account["id"],
                account.get("user_id", ""),
                account["platform"],
                account.get("auth_method", "oauth2"),
                account.get("platform_uid", ""),
                account.get("username", ""),
                account["nickname"],
                account.get("avatar_url", ""),
                account.get("status", "active"),
                account.get("cookie_path", ""),
                account.get("last_login_at"),
                account.get("created_at", datetime.now().isoformat()),
                account.get("updated_at", datetime.now().isoformat()),
            ))
            self.conn.commit()

    def get_platform_accounts(self, platform: Optional[str] = None) -> List[dict]:
        """获取平台账号列表"""
        with self._lock:
            if platform:
                cursor = self.conn.execute(
                    "SELECT * FROM platform_accounts WHERE platform = ? ORDER BY created_at DESC",
                    (platform,),
                )
            else:
                cursor = self.conn.execute(
                    "SELECT * FROM platform_accounts ORDER BY created_at DESC"
                )
            return [dict(row) for row in cursor.fetchall()]

    def get_platform_account(self, account_id: str) -> Optional[dict]:
        """获取单个平台账号"""
        with self._lock:
            cursor = self.conn.execute(
                "SELECT * FROM platform_accounts WHERE id = ?", (account_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_platform_account_by_uid(self, platform: str, platform_uid: str) -> Optional[dict]:
        """按平台 + 平台用户ID 查找账号"""
        with self._lock:
            cursor = self.conn.execute(
                "SELECT * FROM platform_accounts WHERE platform = ? AND platform_uid = ?",
                (platform, platform_uid),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_platform_account(self, account_id: str, **fields) -> None:
        """更新平台账号字段"""
        if not fields:
            return
        allowed = {
            "username", "nickname", "avatar_url", "status",
            "cookie_path", "last_login_at",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = datetime.now().isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [account_id]
        with self._lock:
            self.conn.execute(
                f"UPDATE platform_accounts SET {set_clause} WHERE id = ?",
                tuple(values),
            )
            self.conn.commit()

    def delete_platform_account(self, account_id: str) -> None:
        """删除平台账号（级联删除 oauth_credentials）"""
        with self._lock:
            self.conn.execute(
                "DELETE FROM platform_accounts WHERE id = ?", (account_id,),
            )
            self.conn.commit()

    # ========== oauth_credentials 方法 (Sprint 1) ==========

    def upsert_oauth_credential(
        self,
        *,
        account_id: str,
        platform: str,
        access_token: str,
        refresh_token: str,
        expires_at: int,
        refresh_expires_at: Optional[int] = None,
        raw: str = "",
    ) -> None:
        """插入或更新 OAuth 凭证"""
        import uuid
        now = datetime.now().isoformat()
        with self._lock:
            self.conn.execute("""
                INSERT INTO oauth_credentials (
                    id, account_id, platform, access_token, refresh_token,
                    expires_at, refresh_expires_at, raw, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, platform) DO UPDATE SET
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at = excluded.expires_at,
                    refresh_expires_at = excluded.refresh_expires_at,
                    raw = excluded.raw,
                    updated_at = excluded.updated_at
            """, (
                str(uuid.uuid4()),
                account_id,
                platform,
                access_token,
                refresh_token,
                expires_at,
                refresh_expires_at,
                raw,
                now,
                now,
            ))
            self.conn.commit()

    def get_oauth_credential(self, account_id: str) -> Optional[dict]:
        """获取账号的 OAuth 凭证"""
        with self._lock:
            cursor = self.conn.execute(
                "SELECT * FROM oauth_credentials WHERE account_id = ?",
                (account_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_oauth_credential(self, account_id: str) -> None:
        """删除账号的 OAuth 凭证"""
        with self._lock:
            self.conn.execute(
                "DELETE FROM oauth_credentials WHERE account_id = ?",
                (account_id,),
            )
            self.conn.commit()

    # ========== publish_tasks_v2 方法 (Sprint 1) ==========

    def insert_publish_task_v2(self, task: dict) -> None:
        """插入 v2 发布任务"""
        now = datetime.now().isoformat()
        with self._lock:
            self.conn.execute("""
                INSERT INTO publish_tasks_v2 (
                    id, user_id, account_id, platform, title, description,
                    tags, video_path, cover_path, status, scheduled_at,
                    attempts, max_attempts, error_message, post_id, permalink,
                    platform_options, created_at, updated_at, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task["id"],
                task.get("user_id", ""),
                task["account_id"],
                task["platform"],
                task["title"],
                task.get("description", ""),
                json.dumps(task.get("tags", []), ensure_ascii=False),
                task.get("video_path", ""),
                task.get("cover_path", ""),
                task.get("status", "pending"),
                task.get("scheduled_at"),
                task.get("attempts", 0),
                task.get("max_attempts", 3),
                task.get("error_message", ""),
                task.get("post_id", ""),
                task.get("permalink", ""),
                json.dumps(task.get("platform_options", {}), ensure_ascii=False),
                task.get("created_at", now),
                task.get("updated_at", now),
                task.get("published_at"),
            ))
            self.conn.commit()

    def get_publish_task_v2(self, task_id: str) -> Optional[dict]:
        """获取单个 v2 发布任务"""
        with self._lock:
            cursor = self.conn.execute(
                "SELECT * FROM publish_tasks_v2 WHERE id = ?", (task_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            item["tags"] = json.loads(item.get("tags") or "[]")
            item["platform_options"] = json.loads(item.get("platform_options") or "{}")
            return item

    def get_publish_tasks_v2(
        self,
        *,
        platform: Optional[str] = None,
        status: Optional[str] = None,
        account_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        """获取 v2 发布任务列表（带过滤）"""
        clauses: List[str] = []
        params: List[object] = []
        if platform:
            clauses.append("platform = ?")
            params.append(platform)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if account_id:
            clauses.append("account_id = ?")
            params.append(account_id)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._lock:
            cursor = self.conn.execute(
                f"SELECT * FROM publish_tasks_v2 {where} ORDER BY created_at DESC LIMIT ?",
                tuple(params),
            )
            results = []
            for row in cursor.fetchall():
                item = dict(row)
                item["tags"] = json.loads(item.get("tags") or "[]")
                item["platform_options"] = json.loads(item.get("platform_options") or "{}")
                results.append(item)
            return results

    def update_publish_task_v2(self, task_id: str, **fields) -> None:
        """更新 v2 发布任务字段"""
        if not fields:
            return
        allowed = {
            "status", "attempts", "error_message", "post_id",
            "permalink", "published_at", "scheduled_at",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = datetime.now().isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [task_id]
        with self._lock:
            self.conn.execute(
                f"UPDATE publish_tasks_v2 SET {set_clause} WHERE id = ?",
                tuple(values),
            )
            self.conn.commit()

    def delete_publish_task_v2(self, task_id: str) -> None:
        """删除 v2 发布任务"""
        with self._lock:
            self.conn.execute(
                "DELETE FROM publish_tasks_v2 WHERE id = ?", (task_id,),
            )
            self.conn.commit()

    def close(self):
        """关闭数据库连接"""
        with self._lock:
            self.conn.close()
