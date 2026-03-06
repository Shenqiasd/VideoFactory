# Step 2: 设计 - 发布管理功能

**创建时间**: 2026-03-04 17:10
**设计者**: Claude

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────┐
│                  Web Frontend                    │
│  (publish.html + Alpine.js)                     │
└─────────────────┬───────────────────────────────┘
                  │ HTTP API
┌─────────────────▼───────────────────────────────┐
│              FastAPI Backend                     │
│  - /api/publish/tasks                           │
│  - /api/publish/accounts                        │
│  - /api/publish/stats                           │
└─────────────────┬───────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────┐
│           PublishManager                         │
│  - create_task()                                │
│  - execute_task()                               │
│  - retry_task()                                 │
└─────────────────┬───────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────┐
│      SocialAutoUploadAdapter                    │
│  - call_douyin()                                │
│  - call_bilibili()                              │
│  - call_xiaohongshu()                           │
│  - call_youtube()                               │
│  - call_weixin()                                │
└─────────────────┬───────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────┐
│         social-auto-upload                      │
│  (External Python Scripts)                      │
└─────────────────────────────────────────────────┘
```

---

## 📊 数据模型设计

### 数据库表结构

```sql
-- 发布任务表
CREATE TABLE publish_tasks (
    id TEXT PRIMARY KEY,
    task_id TEXT,  -- 关联的翻译任务
    video_path TEXT NOT NULL,
    platform TEXT NOT NULL,  -- douyin/bilibili/xiaohongshu/youtube/weixin
    account_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    tags TEXT,  -- JSON array
    cover_path TEXT,
    publish_time TEXT,  -- ISO format, null = immediate
    status TEXT NOT NULL,  -- pending/publishing/success/failed/cancelled
    publish_url TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

-- 账号表
CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    name TEXT NOT NULL,
    cookie_path TEXT NOT NULL,
    status TEXT NOT NULL,  -- active/inactive
    last_test TEXT,
    created_at TEXT NOT NULL
);
```

### Python 数据类

```python
# src/distribute/models.py
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime

@dataclass
class PublishTask:
    id: str
    task_id: Optional[str]
    video_path: str
    platform: str
    account_id: str
    title: str
    description: Optional[str]
    tags: List[str]
    cover_path: Optional[str]
    publish_time: Optional[datetime]
    status: str
    publish_url: Optional[str]
    error: Optional[str]
    created_at: datetime
    updated_at: datetime

@dataclass
class Account:
    id: str
    platform: str
    name: str
    cookie_path: str
    status: str
    last_test: Optional[datetime]
    created_at: datetime
```

---

## 🔧 后端实现设计

### 1. PublishManager

```python
# src/distribute/manager.py
import json
import subprocess
from pathlib import Path
from typing import List, Dict, Optional
from .models import PublishTask, Account
from ..core.database import Database

class PublishManager:
    def __init__(self, config, db: Database):
        self.config = config
        self.db = db
        self.adapter = SocialAutoUploadAdapter(config)

    def create_task(self, task_data: Dict) -> PublishTask:
        """创建发布任务"""
        task_id = f"pub_{int(time.time())}"
        task = PublishTask(
            id=task_id,
            status="pending",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            **task_data
        )
        self.db.insert_publish_task(task)
        return task

    def get_tasks(self, platform: Optional[str] = None) -> List[PublishTask]:
        """获取任务列表"""
        return self.db.get_publish_tasks(platform)

    def execute_task(self, task_id: str) -> bool:
        """执行发布任务"""
        task = self.db.get_publish_task(task_id)
        account = self.db.get_account(task.account_id)

        # 更新状态为发布中
        self.db.update_task_status(task_id, "publishing")

        try:
            # 调用 social-auto-upload
            result = self.adapter.publish(
                platform=task.platform,
                video_path=task.video_path,
                title=task.title,
                description=task.description,
                tags=task.tags,
                cover_path=task.cover_path,
                account_file=account.cookie_path,
                publish_time=task.publish_time
            )

            # 更新为成功
            self.db.update_task_result(
                task_id,
                status="success",
                publish_url=result.get("url", "")
            )
            return True

        except Exception as e:
            # 更新为失败
            self.db.update_task_result(
                task_id,
                status="failed",
                error=str(e)
            )
            return False

    def retry_task(self, task_id: str) -> bool:
        """重试失败任务"""
        task = self.db.get_publish_task(task_id)
        if task.status != "failed":
            raise ValueError("Only failed tasks can be retried")

        # 重置状态
        self.db.update_task_status(task_id, "pending")
        return self.execute_task(task_id)
```

### 2. SocialAutoUploadAdapter

```python
# src/distribute/adapter.py
import json
import subprocess
from pathlib import Path
from typing import Dict, Optional, List

class SocialAutoUploadAdapter:
    def __init__(self, config):
        self.upload_path = Path(config.distribute.social_auto_upload_path)
        self.account_dir = Path(config.distribute.account_dir)

    def publish(
        self,
        platform: str,
        video_path: str,
        title: str,
        description: Optional[str],
        tags: List[str],
        cover_path: Optional[str],
        account_file: str,
        publish_time: Optional[str]
    ) -> Dict:
        """调用 social-auto-upload 发布视频"""

        # 生成配置文件
        config = {
            "title": title,
            "file_path": video_path,
            "tags": tags,
            "account_file": account_file
        }

        if description:
            config["description"] = description
        if cover_path:
            config["cover_path"] = cover_path
        if publish_time:
            config["publish_date"] = publish_time.strftime("%Y-%m-%d %H:%M:%S")

        config_file = Path(f"/tmp/publish_config_{platform}_{int(time.time())}.json")
        config_file.write_text(json.dumps(config, ensure_ascii=False))

        # 调用对应平台脚本
        script_map = {
            "douyin": "upload_video_to_douyin.py",
            "bilibili": "upload_video_to_bilibili.py",
            "xiaohongshu": "upload_video_to_xiaohongshu.py",
            "youtube": "upload_video_to_youtube.py",
            "weixin": "upload_video_to_weixin.py"
        }

        script = self.upload_path / script_map[platform]
        cmd = ["python3", str(script), "--config", str(config_file)]

        # 执行
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode != 0:
            raise Exception(f"Upload failed: {result.stderr}")

        # 解析输出提取 URL
        output = result.stdout
        url = self._extract_url(output, platform)

        return {"url": url, "output": output}

    def _extract_url(self, output: str, platform: str) -> str:
        """从输出中提取发布 URL"""
        # 根据不同平台的输出格式提取 URL
        # 这里需要根据实际输出调整
        for line in output.split("\n"):
            if "http" in line:
                return line.strip()
        return ""
```

### 3. Database 扩展

```python
# src/core/database.py (扩展)
class Database:
    def insert_publish_task(self, task: PublishTask):
        """插入发布任务"""
        self.conn.execute("""
            INSERT INTO publish_tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task.id, task.task_id, task.video_path, task.platform,
            task.account_id, task.title, task.description,
            json.dumps(task.tags), task.cover_path,
            task.publish_time.isoformat() if task.publish_time else None,
            task.status, task.publish_url, task.error,
            task.created_at.isoformat(), task.updated_at.isoformat()
        ))
        self.conn.commit()

    def get_publish_tasks(self, platform: Optional[str] = None) -> List[PublishTask]:
        """获取任务列表"""
        if platform:
            cursor = self.conn.execute(
                "SELECT * FROM publish_tasks WHERE platform = ? ORDER BY created_at DESC",
                (platform,)
            )
        else:
            cursor = self.conn.execute(
                "SELECT * FROM publish_tasks ORDER BY created_at DESC"
            )
        return [self._row_to_publish_task(row) for row in cursor.fetchall()]

    def update_task_status(self, task_id: str, status: str):
        """更新任务状态"""
        self.conn.execute(
            "UPDATE publish_tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, datetime.now().isoformat(), task_id)
        )
        self.conn.commit()

    def insert_account(self, account: Account):
        """插入账号"""
        self.conn.execute("""
            INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            account.id, account.platform, account.name,
            account.cookie_path, account.status,
            account.last_test.isoformat() if account.last_test else None,
            account.created_at.isoformat()
        ))
        self.conn.commit()

    def get_accounts(self, platform: Optional[str] = None) -> List[Account]:
        """获取账号列表"""
        if platform:
            cursor = self.conn.execute(
                "SELECT * FROM accounts WHERE platform = ?", (platform,)
            )
        else:
            cursor = self.conn.execute("SELECT * FROM accounts")
        return [self._row_to_account(row) for row in cursor.fetchall()]
```

---

## 🌐 API 端点设计

### 发布任务 API

```python
# web/app.py (扩展)

@app.post("/api/publish/tasks")
async def create_publish_task(request: Request):
    """创建发布任务"""
    data = await request.json()
    task = publish_manager.create_task(data)
    return {"success": True, "task_id": task.id}

@app.get("/api/publish/tasks")
async def get_publish_tasks(platform: Optional[str] = None):
    """获取任务列表"""
    tasks = publish_manager.get_tasks(platform)
    return {
        "tasks": [
            {
                "id": t.id,
                "task_id": t.task_id,
                "video_path": t.video_path,
                "platform": t.platform,
                "title": t.title,
                "status": t.status,
                "publish_url": t.publish_url,
                "created_at": t.created_at.isoformat()
            }
            for t in tasks
        ]
    }

@app.post("/api/publish/tasks/{task_id}/execute")
async def execute_publish_task(task_id: str, background_tasks: BackgroundTasks):
    """执行发布任务"""
    background_tasks.add_task(publish_manager.execute_task, task_id)
    return {"success": True, "message": "Publishing started"}

@app.post("/api/publish/tasks/{task_id}/retry")
async def retry_publish_task(task_id: str):
    """重试失败任务"""
    success = publish_manager.retry_task(task_id)
    return {"success": success}

@app.delete("/api/publish/tasks/{task_id}")
async def delete_publish_task(task_id: str):
    """删除任务"""
    db.delete_publish_task(task_id)
    return {"success": True}
```

### 账号管理 API

```python
@app.post("/api/publish/accounts")
async def create_account(request: Request):
    """添加账号"""
    data = await request.json()

    # 保存 cookie 文件
    platform = data["platform"]
    name = data["name"]
    cookie_content = data["cookie"]

    account_dir = Path(config.distribute.account_dir) / platform
    account_dir.mkdir(parents=True, exist_ok=True)

    cookie_file = account_dir / f"{name}.json"
    cookie_file.write_text(cookie_content)

    # 创建账号记录
    account = Account(
        id=f"acc_{int(time.time())}",
        platform=platform,
        name=name,
        cookie_path=str(cookie_file),
        status="active",
        last_test=None,
        created_at=datetime.now()
    )
    db.insert_account(account)

    return {"success": True, "account_id": account.id}

@app.get("/api/publish/accounts")
async def get_accounts(platform: Optional[str] = None):
    """获取账号列表"""
    accounts = db.get_accounts(platform)
    return {
        "accounts": [
            {
                "id": a.id,
                "platform": a.platform,
                "name": a.name,
                "status": a.status,
                "last_test": a.last_test.isoformat() if a.last_test else None
            }
            for a in accounts
        ]
    }

@app.delete("/api/publish/accounts/{account_id}")
async def delete_account(account_id: str):
    """删除账号"""
    account = db.get_account(account_id)
    Path(account.cookie_path).unlink(missing_ok=True)
    db.delete_account(account_id)
    return {"success": True}

@app.post("/api/publish/accounts/{account_id}/test")
async def test_account(account_id: str):
    """测试账号可用性"""
    # 简单测试：检查 cookie 文件是否存在且有效
    account = db.get_account(account_id)
    cookie_file = Path(account.cookie_path)

    if not cookie_file.exists():
        return {"success": False, "error": "Cookie file not found"}

    try:
        cookie_data = json.loads(cookie_file.read_text())
        if not cookie_data:
            return {"success": False, "error": "Empty cookie"}

        db.update_account_test_time(account_id, datetime.now())
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
```

### 统计 API

```python
@app.get("/api/publish/stats")
async def get_publish_stats():
    """获取统计数据"""
    tasks = db.get_publish_tasks()
    today = datetime.now().date()

    stats = {
        "pending": len([t for t in tasks if t.status == "pending"]),
        "publishing": len([t for t in tasks if t.status == "publishing"]),
        "today_success": len([
            t for t in tasks
            if t.status == "success" and t.updated_at.date() == today
        ]),
        "failed": len([t for t in tasks if t.status == "failed"]),
        "total": len(tasks)
    }

    if stats["total"] > 0:
        stats["success_rate"] = (
            len([t for t in tasks if t.status == "success"]) / stats["total"] * 100
        )
    else:
        stats["success_rate"] = 0

    return stats
```

---

## 🎨 前端实现设计

### 发布队列页面

```html
<!-- web/templates/publish.html -->
<div x-data="publishApp()">
    <!-- 统计卡片 -->
    <div class="grid grid-cols-4 gap-4 mb-6">
        <div class="stat-card">
            <span>待发布</span>
            <span x-text="stats.pending"></span>
        </div>
        <div class="stat-card">
            <span>发布中</span>
            <span x-text="stats.publishing"></span>
        </div>
        <div class="stat-card">
            <span>今日成功</span>
            <span x-text="stats.today_success"></span>
        </div>
        <div class="stat-card">
            <span>失败</span>
            <span x-text="stats.failed"></span>
        </div>
    </div>

    <!-- 平台筛选 -->
    <div class="flex gap-2 mb-4">
        <button @click="filterPlatform(null)" :class="{'active': platform === null}">
            全部
        </button>
        <button @click="filterPlatform('douyin')" :class="{'active': platform === 'douyin'}">
            抖音
        </button>
        <button @click="filterPlatform('bilibili')" :class="{'active': platform === 'bilibili'}">
            B站
        </button>
        <!-- 其他平台 -->
    </div>

    <!-- 任务列表 -->
    <table>
        <thead>
            <tr>
                <th>标题</th>
                <th>平台</th>
                <th>状态</th>
                <th>创建时间</th>
                <th>操作</th>
            </tr>
        </thead>
        <tbody>
            <template x-for="task in tasks" :key="task.id">
                <tr>
                    <td x-text="task.title"></td>
                    <td x-text="task.platform"></td>
                    <td>
                        <span :class="statusClass(task.status)" x-text="task.status"></span>
                    </td>
                    <td x-text="formatTime(task.created_at)"></td>
                    <td>
                        <button x-show="task.status === 'pending'" @click="executeTask(task.id)">
                            发布
                        </button>
                        <button x-show="task.status === 'failed'" @click="retryTask(task.id)">
                            重试
                        </button>
                        <a x-show="task.status === 'success' && task.publish_url"
                           :href="task.publish_url" target="_blank">
                            查看
                        </a>
                    </td>
                </tr>
            </template>
        </tbody>
    </table>
</div>

<script>
function publishApp() {
    return {
        tasks: [],
        stats: {},
        platform: null,

        init() {
            this.loadTasks();
            this.loadStats();
            setInterval(() => this.loadStats(), 10000);
        },

        async loadTasks() {
            const url = this.platform
                ? `/api/publish/tasks?platform=${this.platform}`
                : '/api/publish/tasks';
            const res = await fetch(url);
            const data = await res.json();
            this.tasks = data.tasks;
        },

        async loadStats() {
            const res = await fetch('/api/publish/stats');
            this.stats = await res.json();
        },

        filterPlatform(p) {
            this.platform = p;
            this.loadTasks();
        },

        async executeTask(taskId) {
            await fetch(`/api/publish/tasks/${taskId}/execute`, {method: 'POST'});
            this.loadTasks();
        },

        async retryTask(taskId) {
            await fetch(`/api/publish/tasks/${taskId}/retry`, {method: 'POST'});
            this.loadTasks();
        }
    }
}
</script>
```

---

## 🔐 配置文件设计

```yaml
# config/settings.yaml (新增)
distribute:
  auto_publish: false
  social_auto_upload_path: "/path/to/social-auto-upload"
  account_dir: "/path/to/accounts"
  publish_max_retries: 2
  retry_backoff_seconds: 60

  # 平台配置
  platforms:
    douyin:
      enabled: true
    bilibili:
      enabled: true
    xiaohongshu:
      enabled: true
    youtube:
      enabled: true
    weixin:
      enabled: true
```

---

## ✅ 关键技术点

1. **异步执行**: 使用 FastAPI BackgroundTasks 避免阻塞
2. **错误处理**: 捕获 subprocess 异常，记录详细错误
3. **重试机制**: 失败任务可重试，配置最大重试次数
4. **URL 提取**: 从 social-auto-upload 输出解析发布 URL
5. **Cookie 管理**: 按平台分目录存储账号 cookie
6. **状态同步**: 前端定时轮询更新任务状态

---

## 📝 实施注意事项

1. **social-auto-upload 路径**: 需要在配置中正确设置
2. **Python 环境**: 确保 social-auto-upload 依赖已安装
3. **超时设置**: 发布可能耗时较长，设置合理超时
4. **并发控制**: 避免同时发布过多任务
5. **日志记录**: 记录完整的发布过程和输出
