# Step 2: 技术设计 - 存储管理删除功能

**创建时间**: 2026-03-04 16:40
**设计者**: Claude
**状态**: 设计中

---

## 🎯 设计目标

1. **文件列表展示**：R2 + 本地文件列表（大小、时间）
2. **手动删除**：单个/批量删除 + 确认
3. **定时清理**：按目录配置清理周期 + 手动触发
4. **空间监控**：实时显示使用量

---

## 🏗️ 整体架构

```
前端 (storage.html)
  ↓
API 层 (web/app.py)
  ↓
存储管理层 (core/storage.py)
  ├─ StorageManager (R2)
  └─ LocalStorage (本地)
  ↓
定时任务 (scheduler)
  └─ 自动清理过期文件
```

---

## 📦 模块设计

### 模块 1: 文件列表 API

**后端接口**:

```python
# web/app.py

@app.get("/api/storage/files")
async def get_storage_files(
    location: str = "r2",  # r2 / local
    path: str = "raw"      # raw / processed / ready / archive / working / output
):
    """
    获取文件列表

    Returns:
        {
            "files": [
                {
                    "name": "video1.mp4",
                    "size": 125829120,
                    "size_human": "120 MB",
                    "modified": "2026-03-04T14:30:00Z",
                    "modified_human": "2 hours ago",
                    "path": "raw/video1.mp4"
                }
            ],
            "total_size": 125829120,
            "total_size_human": "120 MB",
            "count": 1
        }
    """
    if location == "r2":
        storage = StorageManager()
        files = storage.list_files_with_details(path)
    else:
        local_storage = LocalStorage()
        files = local_storage.list_files_with_details(path)

    return {
        "files": files,
        "total_size": sum(f["size"] for f in files),
        "count": len(files)
    }
```

**StorageManager 扩展**:

```python
# src/core/storage.py

class StorageManager:
    def list_files_with_details(self, r2_path: str = "") -> List[Dict]:
        """
        列出文件详情

        使用 rclone lsjson 获取详细信息
        """
        try:
            full_r2_path = f"{self.r2_prefix}/{r2_path}" if r2_path else self.r2_prefix
            cmd = ["rclone", "lsjson", full_r2_path, "-R"]  # -R 递归
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0:
                import json
                files = json.loads(result.stdout)
                return [
                    {
                        "name": f["Name"],
                        "size": f["Size"],
                        "size_human": self._format_size(f["Size"]),
                        "modified": f["ModTime"],
                        "modified_human": self._format_time(f["ModTime"]),
                        "path": f"{r2_path}/{f['Path']}" if r2_path else f["Path"]
                    }
                    for f in files if not f["IsDir"]
                ]
            return []
        except Exception as e:
            logger.error(f"列出文件详情异常: {e}")
            return []

    @staticmethod
    def _format_size(bytes: int) -> str:
        """格式化文件大小"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes < 1024:
                return f"{bytes:.1f} {unit}"
            bytes /= 1024
        return f"{bytes:.1f} TB"

    @staticmethod
    def _format_time(iso_time: str) -> str:
        """格式化时间为相对时间"""
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso_time.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        delta = now - dt

        if delta.days > 0:
            return f"{delta.days}d ago"
        elif delta.seconds > 3600:
            return f"{delta.seconds // 3600}h ago"
        elif delta.seconds > 60:
            return f"{delta.seconds // 60}m ago"
        else:
            return "just now"
```

**LocalStorage 扩展**:

```python
class LocalStorage:
    def list_files_with_details(self, path: str = "working") -> List[Dict]:
        """
        列出本地文件详情

        Args:
            path: working / output
        """
        import os
        from datetime import datetime

        if path == "working":
            base_dir = self.working_dir
        elif path == "output":
            base_dir = self.output_dir
        else:
            return []

        files = []
        for root, dirs, filenames in os.walk(base_dir):
            for filename in filenames:
                filepath = Path(root) / filename
                stat = filepath.stat()
                rel_path = filepath.relative_to(base_dir)

                files.append({
                    "name": filename,
                    "size": stat.st_size,
                    "size_human": StorageManager._format_size(stat.st_size),
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "modified_human": StorageManager._format_time(
                        datetime.fromtimestamp(stat.st_mtime).isoformat()
                    ),
                    "path": str(rel_path)
                })

        return files
```

---

## 🗑️ 模块 2: 删除功能

**后端接口**:

```python
@app.delete("/api/storage/files")
async def delete_storage_files(request: Request):
    """
    删除文件

    Body:
        {
            "location": "r2",  # r2 / local
            "paths": ["raw/video1.mp4", "raw/video2.mp4"]
        }
    """
    data = await request.json()
    location = data.get("location", "r2")
    paths = data.get("paths", [])

    if not paths:
        return {"success": False, "error": "No paths provided"}

    try:
        if location == "r2":
            storage = StorageManager()
            deleted = storage.delete_files(paths)
        else:
            local_storage = LocalStorage()
            deleted = local_storage.delete_files(paths)

        return {
            "success": True,
            "deleted": deleted
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }
```

**StorageManager 扩展**:

```python
class StorageManager:
    def delete_files(self, r2_paths: List[str]) -> int:
        """
        批量删除文件

        Args:
            r2_paths: R2 相对路径列表

        Returns:
            int: 成功删除的文件数
        """
        deleted = 0
        for r2_path in r2_paths:
            if self.delete_from_r2(r2_path):
                deleted += 1
        return deleted
```

**LocalStorage 扩展**:

```python
class LocalStorage:
    def delete_files(self, paths: List[str]) -> int:
        """
        批量删除本地文件

        Args:
            paths: 相对路径列表

        Returns:
            int: 成功删除的文件数
        """
        deleted = 0
        for path in paths:
            # 尝试从 working 和 output 目录删除
            for base_dir in [self.working_dir, self.output_dir]:
                filepath = base_dir / path
                if filepath.exists():
                    try:
                        filepath.unlink()
                        logger.info(f"🗑️ 删除文件: {filepath}")
                        deleted += 1
                        break
                    except Exception as e:
                        logger.error(f"删除文件失败: {filepath}, {e}")
        return deleted
```

---

## ⏰ 模块 3: 定时清理

**配置设计**:

```yaml
# config/settings.yaml

storage:
  auto_cleanup:
    enabled: true
    schedule: "0 2 * * *"  # Cron 表达式：每天凌晨 2 点

    rules:
      - location: "local"
        path: "working"
        days: 1
        enabled: true

      - location: "local"
        path: "output"
        days: 3
        enabled: true

      - location: "r2"
        path: "raw"
        days: 7
        enabled: true

      - location: "r2"
        path: "processed"
        days: 30
        enabled: true

      - location: "r2"
        path: "ready"
        days: 7
        enabled: true
```

**后端接口**:

```python
@app.post("/api/storage/cleanup")
async def cleanup_storage(request: Request):
    """
    手动触发清理

    Body:
        {
            "location": "r2",  # r2 / local / all
            "path": "raw",     # 可选
            "days": 7          # 可选
        }
    """
    data = await request.json()
    location = data.get("location", "all")
    path = data.get("path")
    days = data.get("days")

    try:
        result = await run_cleanup(location, path, days)
        return {
            "success": True,
            **result
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/api/storage/cleanup-config")
async def get_cleanup_config():
    """获取清理配置"""
    config = Config()
    return config.get("storage.auto_cleanup", default={})


@app.put("/api/storage/cleanup-config")
async def update_cleanup_config(request: Request):
    """更新清理配置"""
    data = await request.json()
    config = Config()
    config.set("storage.auto_cleanup", data)
    config.save()
    return {"success": True}
```

**清理逻辑**:

```python
# src/core/storage.py

class StorageManager:
    def cleanup_old_files(self, r2_path: str, days: int) -> Dict:
        """
        清理过期文件

        Args:
            r2_path: R2 路径
            days: 保留天数

        Returns:
            {
                "deleted": 5,
                "freed_bytes": 524288000,
                "freed_human": "500 MB"
            }
        """
        from datetime import datetime, timedelta, timezone

        cutoff_time = datetime.now(timezone.utc) - timedelta(days=days)
        files = self.list_files_with_details(r2_path)

        deleted = 0
        freed_bytes = 0

        for file in files:
            file_time = datetime.fromisoformat(file["modified"].replace('Z', '+00:00'))
            if file_time < cutoff_time:
                if self.delete_from_r2(file["path"]):
                    deleted += 1
                    freed_bytes += file["size"]
                    logger.info(f"🗑️ 清理过期文件: {file['path']}")

        return {
            "deleted": deleted,
            "freed_bytes": freed_bytes,
            "freed_human": self._format_size(freed_bytes)
        }


class LocalStorage:
    def cleanup_old_files(self, path: str, days: int) -> Dict:
        """清理本地过期文件"""
        from datetime import datetime, timedelta

        cutoff_time = datetime.now() - timedelta(days=days)

        if path == "working":
            base_dir = self.working_dir
        elif path == "output":
            base_dir = self.output_dir
        else:
            return {"deleted": 0, "freed_bytes": 0}

        deleted = 0
        freed_bytes = 0

        for root, dirs, filenames in os.walk(base_dir):
            for filename in filenames:
                filepath = Path(root) / filename
                stat = filepath.stat()
                file_time = datetime.fromtimestamp(stat.st_mtime)

                if file_time < cutoff_time:
                    try:
                        freed_bytes += stat.st_size
                        filepath.unlink()
                        deleted += 1
                        logger.info(f"🗑️ 清理过期文件: {filepath}")
                    except Exception as e:
                        logger.error(f"清理文件失败: {filepath}, {e}")

        return {
            "deleted": deleted,
            "freed_bytes": freed_bytes,
            "freed_human": StorageManager._format_size(freed_bytes)
        }
```

**定时任务**:

```python
# src/core/scheduler.py (新增)

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

class StorageCleanupScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.config = Config()

    def start(self):
        """启动定时任务"""
        cleanup_config = self.config.get("storage.auto_cleanup", default={})

        if not cleanup_config.get("enabled", False):
            logger.info("自动清理未启用")
            return

        schedule = cleanup_config.get("schedule", "0 2 * * *")
        trigger = CronTrigger.from_crontab(schedule)

        self.scheduler.add_job(
            self.run_cleanup,
            trigger=trigger,
            id="storage_cleanup"
        )

        self.scheduler.start()
        logger.info(f"✅ 存储清理定时任务已启动: {schedule}")

    async def run_cleanup(self):
        """执行清理任务"""
        cleanup_config = self.config.get("storage.auto_cleanup", default={})
        rules = cleanup_config.get("rules", [])

        for rule in rules:
            if not rule.get("enabled", True):
                continue

            location = rule["location"]
            path = rule["path"]
            days = rule["days"]

            try:
                if location == "r2":
                    storage = StorageManager()
                    result = storage.cleanup_old_files(path, days)
                else:
                    local_storage = LocalStorage()
                    result = local_storage.cleanup_old_files(path, days)

                logger.info(
                    f"✅ 清理完成: {location}:{path}, "
                    f"删除 {result['deleted']} 个文件, "
                    f"释放 {result['freed_human']}"
                )
            except Exception as e:
                logger.error(f"清理失败: {location}:{path}, {e}")
```

---

## 🖥️ 前端设计

### 文件列表界面

```html
<!-- web/templates/storage.html -->

<div x-data="storageManager()">
    <!-- 目录切换 -->
    <div class="flex gap-2 mb-4">
        <button @click="switchLocation('r2')"
                :class="location === 'r2' ? 'bg-fg-strong text-white' : 'bg-muted'">
            R2 云存储
        </button>
        <button @click="switchLocation('local')"
                :class="location === 'local' ? 'bg-fg-strong text-white' : 'bg-muted'">
            本地存储
        </button>
    </div>

    <!-- R2 路径选择 -->
    <div x-show="location === 'r2'" class="flex gap-2 mb-4">
        <button @click="switchPath('raw')" :class="pathClass('raw')">raw</button>
        <button @click="switchPath('processed')" :class="pathClass('processed')">processed</button>
        <button @click="switchPath('ready')" :class="pathClass('ready')">ready</button>
        <button @click="switchPath('archive')" :class="pathClass('archive')">archive</button>
    </div>

    <!-- 本地路径选择 -->
    <div x-show="location === 'local'" class="flex gap-2 mb-4">
        <button @click="switchPath('working')" :class="pathClass('working')">working</button>
        <button @click="switchPath('output')" :class="pathClass('output')">output</button>
    </div>

    <!-- 文件列表 -->
    <table class="w-full">
        <thead>
            <tr>
                <th><input type="checkbox" @change="toggleAll" /></th>
                <th>文件名</th>
                <th>大小</th>
                <th>修改时间</th>
                <th>操作</th>
            </tr>
        </thead>
        <tbody>
            <template x-for="file in files" :key="file.path">
                <tr>
                    <td><input type="checkbox" :value="file.path" x-model="selected" /></td>
                    <td x-text="file.name"></td>
                    <td x-text="file.size_human"></td>
                    <td x-text="file.modified_human"></td>
                    <td>
                        <button @click="deleteFile(file.path)">删除</button>
                    </td>
                </tr>
            </template>
        </tbody>
    </table>

    <!-- 批量操作 -->
    <div class="flex gap-2 mt-4">
        <button @click="deleteSelected" :disabled="selected.length === 0">
            删除选中 (<span x-text="selected.length"></span>)
        </button>
        <button @click="cleanupOldFiles">清理过期文件</button>
    </div>
</div>

<script>
function storageManager() {
    return {
        location: 'r2',
        path: 'raw',
        files: [],
        selected: [],

        async init() {
            await this.loadFiles();
        },

        async loadFiles() {
            const resp = await fetch(`/api/storage/files?location=${this.location}&path=${this.path}`);
            const data = await resp.json();
            this.files = data.files;
        },

        switchLocation(loc) {
            this.location = loc;
            this.path = loc === 'r2' ? 'raw' : 'working';
            this.selected = [];
            this.loadFiles();
        },

        switchPath(p) {
            this.path = p;
            this.selected = [];
            this.loadFiles();
        },

        async deleteFile(path) {
            if (!confirm('确定删除此文件？')) return;
            await this.deleteFiles([path]);
        },

        async deleteSelected() {
            if (!confirm(`确定删除选中的 ${this.selected.length} 个文件？`)) return;
            await this.deleteFiles(this.selected);
        },

        async deleteFiles(paths) {
            const resp = await fetch('/api/storage/files', {
                method: 'DELETE',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    location: this.location,
                    paths: paths
                })
            });
            const data = await resp.json();
            if (data.success) {
                alert(`成功删除 ${data.deleted} 个文件`);
                this.selected = [];
                await this.loadFiles();
            }
        },

        async cleanupOldFiles() {
            const days = prompt('清理多少天前的文件？', '7');
            if (!days) return;

            const resp = await fetch('/api/storage/cleanup', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    location: this.location,
                    path: this.path,
                    days: parseInt(days)
                })
            });
            const data = await resp.json();
            if (data.success) {
                alert(`清理完成：删除 ${data.deleted} 个文件，释放 ${data.freed_human}`);
                await this.loadFiles();
            }
        },

        pathClass(p) {
            return this.path === p ? 'bg-fg-strong text-white' : 'bg-muted';
        },

        toggleAll(e) {
            this.selected = e.target.checked ? this.files.map(f => f.path) : [];
        }
    }
}
</script>
```

---

## 📋 模块影响清单

### 新增文件 (2个)

```
src/core/scheduler.py          # ~100 行
tests/test_storage_cleanup.py  # ~150 行
```

### 修改文件 (4个)

```
src/core/storage.py
- 新增 list_files_with_details()
- 新增 delete_files()
- 新增 cleanup_old_files()
- 新增 _format_size(), _format_time()

web/app.py
- 新增 GET /api/storage/files
- 新增 DELETE /api/storage/files
- 新增 POST /api/storage/cleanup
- 新增 GET/PUT /api/storage/cleanup-config

web/templates/storage.html
- 完整重写文件列表界面
- 新增 Alpine.js 交互逻辑

config/settings.yaml
- 新增 storage.auto_cleanup 配置段
```

### 依赖变更

```
requirements.txt 新增:
apscheduler>=3.10.0
```

---

## ⚙️ 完整配置

```yaml
# config/settings.yaml

storage:
  r2:
    bucket: "videoflow"
    rclone_remote: "r2"

  local:
    mac_working_dir: "/tmp/video-factory/working"
    mac_output_dir: "/tmp/video-factory/output"

  auto_cleanup:
    enabled: true
    schedule: "0 2 * * *"  # 每天凌晨 2 点

    rules:
      - location: "local"
        path: "working"
        days: 1
        enabled: true

      - location: "local"
        path: "output"
        days: 3
        enabled: true

      - location: "r2"
        path: "raw"
        days: 7
        enabled: true

      - location: "r2"
        path: "processed"
        days: 30
        enabled: true

      - location: "r2"
        path: "ready"
        days: 7
        enabled: true

      - location: "r2"
        path: "archive"
        days: 90
        enabled: true
```

---

## 📅 实施计划

### Day 1: 文件列表
- [ ] 扩展 StorageManager.list_files_with_details()
- [ ] 扩展 LocalStorage.list_files_with_details()
- [ ] 实现 GET /api/storage/files
- [ ] 前端文件列表展示

### Day 2: 删除功能
- [ ] 扩展 StorageManager.delete_files()
- [ ] 扩展 LocalStorage.delete_files()
- [ ] 实现 DELETE /api/storage/files
- [ ] 前端删除按钮 + 批量删除

### Day 3: 清理功能
- [ ] 实现 cleanup_old_files()
- [ ] 实现 POST /api/storage/cleanup
- [ ] 实现清理配置接口
- [ ] 前端清理界面

### Day 4: 定时任务
- [ ] 创建 StorageCleanupScheduler
- [ ] 集成到主服务
- [ ] 测试定时清理

---

## ✅ 完成定义

- [ ] 文件列表展示（R2 + 本地）
- [ ] 手动删除（单个 + 批量）
- [ ] 清理配置界面
- [ ] 手动触发清理
- [ ] 定时自动清理
- [ ] 测试通过


---

## 🖥️ 模块 4: 前端设计

### 文件列表界面

```html
<!-- web/templates/storage.html -->

<div x-data="storageManager()">
    <!-- 目录切换 -->
    <div class="flex gap-2 mb-4">
        <button @click="switchLocation('r2')" 
                :class="location === 'r2' ? 'bg-fg-strong text-white' : 'bg-muted'">
            R2 云存储
        </button>
        <button @click="switchLocation('local')" 
                :class="location === 'local' ? 'bg-fg-strong text-white' : 'bg-muted'">
            本地存储
        </button>
    </div>

    <!-- R2 路径选择 -->
    <div x-show="location === 'r2'" class="flex gap-2 mb-4">
        <button @click="switchPath('raw')" :class="pathClass('raw')">raw</button>
        <button @click="switchPath('processed')" :class="pathClass('processed')">processed</button>
        <button @click="switchPath('ready')" :class="pathClass('ready')">ready</button>
        <button @click="switchPath('archive')" :class="pathClass('archive')">archive</button>
    </div>

    <!-- 本地路径选择 -->
    <div x-show="location === 'local'" class="flex gap-2 mb-4">
        <button @click="switchPath('working')" :class="pathClass('working')">working</button>
        <button @click="switchPath('output')" :class="pathClass('output')">output</button>
    </div>

    <!-- 文件列表 -->
    <table class="w-full">
        <thead>
            <tr>
                <th><input type="checkbox" @change="toggleAll" x-model="selectAll"></th>
                <th>文件名</th>
                <th>大小</th>
                <th>修改时间</th>
                <th>操作</th>
            </tr>
        </thead>
        <tbody>
            <template x-for="file in files" :key="file.path">
                <tr>
                    <td><input type="checkbox" :value="file.path" x-model="selected"></td>
                    <td x-text="file.name"></td>
                    <td x-text="file.size_human"></td>
                    <td x-text="file.modified_human"></td>
                    <td>
                        <button @click="deleteFile(file.path)" class="text-red-500">删除</button>
                    </td>
                </tr>
            </template>
        </tbody>
    </table>

    <!-- 批量操作 -->
    <div class="flex gap-2 mt-4">
        <button @click="deleteSelected" :disabled="selected.length === 0">
            批量删除 (<span x-text="selected.length"></span>)
        </button>
        <button @click="cleanupOldFiles">清理过期文件</button>
    </div>
</div>

<script>
function storageManager() {
    return {
        location: 'r2',
        path: 'raw',
        files: [],
        selected: [],
        selectAll: false,

        async init() {
            await this.loadFiles();
        },

        async loadFiles() {
            const resp = await fetch(`/api/storage/files?location=${this.location}&path=${this.path}`);
            const data = await resp.json();
            this.files = data.files;
        },

        async switchLocation(loc) {
            this.location = loc;
            this.path = loc === 'r2' ? 'raw' : 'working';
            this.selected = [];
            await this.loadFiles();
        },

        async switchPath(p) {
            this.path = p;
            this.selected = [];
            await this.loadFiles();
        },

        pathClass(p) {
            return this.path === p ? 'bg-fg-strong text-white' : 'bg-muted';
        },

        toggleAll() {
            if (this.selectAll) {
                this.selected = this.files.map(f => f.path);
            } else {
                this.selected = [];
            }
        },

        async deleteFile(path) {
            if (!confirm(`确定删除 ${path}?`)) return;
            await this.deleteFiles([path]);
        },

        async deleteSelected() {
            if (!confirm(`确定删除 ${this.selected.length} 个文件?`)) return;
            await this.deleteFiles(this.selected);
        },

        async deleteFiles(paths) {
            const resp = await fetch('/api/storage/files', {
                method: 'DELETE',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    location: this.location,
                    paths: paths
                })
            });
            const data = await resp.json();
            if (data.success) {
                alert(`成功删除 ${data.deleted} 个文件`);
                this.selected = [];
                await this.loadFiles();
            } else {
                alert(`删除失败: ${data.error}`);
            }
        },

        async cleanupOldFiles() {
            const days = prompt('清理多少天前的文件?', '7');
            if (!days) return;

            const resp = await fetch('/api/storage/cleanup', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    location: this.location,
                    path: this.path,
                    days: parseInt(days)
                })
            });
            const data = await resp.json();
            if (data.success) {
                alert(`清理完成: 删除 ${data.deleted} 个文件，释放 ${data.freed_human}`);
                await this.loadFiles();
            } else {
                alert(`清理失败: ${data.error}`);
            }
        }
    }
}
</script>
```

### 清理配置界面

```html
<!-- 在 settings.html 中新增 -->

<div x-show="active === 'storage'" x-transition>
    <h2 class="text-[15px] font-semibold text-fg-strong mb-5">存储清理配置</h2>

    <label class="flex items-center justify-between rounded-xl border border-border px-3 py-2">
        <span class="text-[13px] text-fg">启用自动清理</span>
        <input id="storage-cleanup-enabled" type="checkbox" />
    </label>

    <div class="space-y-3 mt-4">
        <h3 class="text-[13px] font-semibold">清理规则</h3>

        <div class="grid grid-cols-2 gap-4">
            <div>
                <label class="block text-[12px] text-fg-sub mb-1.5">本地工作目录 (天)</label>
                <input id="cleanup-local-working" type="number" min="1" value="1" />
            </div>
            <div>
                <label class="block text-[12px] text-fg-sub mb-1.5">本地输出目录 (天)</label>
                <input id="cleanup-local-output" type="number" min="1" value="3" />
            </div>
        </div>

        <div class="grid grid-cols-2 gap-4">
            <div>
                <label class="block text-[12px] text-fg-sub mb-1.5">R2 原始视频 (天)</label>
                <input id="cleanup-r2-raw" type="number" min="1" value="7" />
            </div>
            <div>
                <label class="block text-[12px] text-fg-sub mb-1.5">R2 成品视频 (天)</label>
                <input id="cleanup-r2-processed" type="number" min="1" value="30" />
            </div>
        </div>

        <div class="grid grid-cols-2 gap-4">
            <div>
                <label class="block text-[12px] text-fg-sub mb-1.5">R2 待分发 (天)</label>
                <input id="cleanup-r2-ready" type="number" min="1" value="7" />
            </div>
            <div>
                <label class="block text-[12px] text-fg-sub mb-1.5">R2 归档 (天)</label>
                <input id="cleanup-r2-archive" type="number" min="1" value="90" />
            </div>
        </div>
    </div>

    <button id="save-cleanup-config" class="w-full h-10 rounded-2xl bg-fg-strong text-white mt-4">
        保存配置
    </button>

    <button id="run-cleanup-now" class="w-full h-10 rounded-2xl border-2 border-border mt-2">
        立即执行清理
    </button>
</div>
```

---

## 📋 模块影响清单

### 新增文件 (1个)

```
src/core/scheduler.py  # 定时任务调度器
```

### 修改文件 (4个)

```
src/core/storage.py
- 新增 list_files_with_details()
- 新增 delete_files()
- 新增 cleanup_old_files()
- 新增 _format_size(), _format_time()

web/app.py
- 新增 GET /api/storage/files
- 新增 DELETE /api/storage/files
- 新增 POST /api/storage/cleanup
- 新增 GET /api/storage/cleanup-config
- 新增 PUT /api/storage/cleanup-config

web/templates/storage.html
- 完整重写文件列表界面
- 新增 Alpine.js 交互逻辑

web/templates/settings.html
- 新增存储清理配置区域

config/settings.yaml
- 新增 storage.auto_cleanup 配置段
```

---

## ⚙️ 配置设计

### config/settings.yaml

```yaml
storage:
  r2:
    bucket: "videoflow"
    rclone_remote: "r2"
    endpoint: "https://..."

  local:
    mac_working_dir: "/tmp/video-factory/working"
    mac_output_dir: "/tmp/video-factory/output"
    max_working_videos: 5

  # 自动清理配置
  auto_cleanup:
    enabled: true
    schedule: "0 2 * * *"  # 每天凌晨 2 点

    rules:
      - location: "local"
        path: "working"
        days: 1
      - location: "local"
        path: "output"
        days: 3
      - location: "r2"
        path: "raw"
        days: 7
      - location: "r2"
        path: "processed"
        days: 30
      - location: "r2"
        path: "ready"
        days: 7
      - location: "r2"
        path: "archive"
        days: 90
```

---

## 📅 实施计划

### Day 1: 文件列表功能
- [ ] 扩展 StorageManager.list_files_with_details()
- [ ] 扩展 LocalStorage.list_files_with_details()
- [ ] 实现 GET /api/storage/files
- [ ] 前端文件列表界面

### Day 2: 删除功能
- [ ] 扩展 StorageManager.delete_files()
- [ ] 扩展 LocalStorage.delete_files()
- [ ] 实现 DELETE /api/storage/files
- [ ] 前端删除按钮 + 批量删除

### Day 3: 清理功能
- [ ] 实现 StorageManager.cleanup_old_files()
- [ ] 实现 LocalStorage.cleanup_old_files()
- [ ] 实现 POST /api/storage/cleanup
- [ ] 前端清理按钮

### Day 4: 定时任务 + 配置
- [ ] 创建 src/core/scheduler.py
- [ ] 实现清理配置接口
- [ ] 前端清理配置界面
- [ ] 集成测试

---

## ✅ 完成定义

- [ ] 文件列表展示（R2 + 本地）
- [ ] 手动删除（单个 + 批量）
- [ ] 清理过期文件（手动触发）
- [ ] 清理配置界面
- [ ] 定时任务自动清理
- [ ] 存储空间监控

**下一步**: 进入 Step 3 实施

