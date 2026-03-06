"""
数据库管理模块 - SQLite
"""
import sqlite3
import json
from pathlib import Path
from typing import Optional, List
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class Database:
    """SQLite 数据库管理器"""

    def __init__(self, db_path: str = "data/video_factory.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        """初始化数据库表"""
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
        self.conn.commit()

    # ========== publish_tasks 方法 ==========

    def insert_publish_task(self, task_data: dict):
        """插入发布任务"""
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
        cursor = self.conn.execute("SELECT * FROM publish_tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_task_status(self, task_id: str, status: str):
        """更新任务状态"""
        self.conn.execute(
            "UPDATE publish_tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, datetime.now().isoformat(), task_id)
        )
        self.conn.commit()

    def update_task_result(self, task_id: str, status: str, publish_url: str = None, error: str = None):
        """更新任务结果"""
        self.conn.execute(
            "UPDATE publish_tasks SET status = ?, publish_url = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, publish_url, error, datetime.now().isoformat(), task_id)
        )
        self.conn.commit()

    def delete_publish_task(self, task_id: str):
        """删除任务"""
        self.conn.execute("DELETE FROM publish_tasks WHERE id = ?", (task_id,))
        self.conn.commit()

    # ========== accounts 方法 ==========

    def insert_account(self, account_data: dict):
        """插入账号"""
        self.conn.execute("""
            INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            account_data['id'],
            account_data['platform'],
            account_data['name'],
            account_data['cookie_path'],
            account_data['status'],
            account_data.get('last_test'),
            account_data['created_at']
        ))
        self.conn.commit()

    def get_accounts(self, platform: Optional[str] = None) -> List[dict]:
        """获取账号列表"""
        if platform:
            cursor = self.conn.execute(
                "SELECT * FROM accounts WHERE platform = ?", (platform,)
            )
        else:
            cursor = self.conn.execute("SELECT * FROM accounts")

        return [dict(row) for row in cursor.fetchall()]

    def get_account(self, account_id: str) -> Optional[dict]:
        """获取单个账号"""
        cursor = self.conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_account_test_time(self, account_id: str, test_time: datetime):
        """更新账号测试时间"""
        self.conn.execute(
            "UPDATE accounts SET last_test = ? WHERE id = ?",
            (test_time.isoformat(), account_id)
        )
        self.conn.commit()

    def delete_account(self, account_id: str):
        """删除账号"""
        self.conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        self.conn.commit()

    def close(self):
        """关闭数据库连接"""
        self.conn.close()
