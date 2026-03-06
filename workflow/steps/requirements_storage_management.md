# 需求分解 - 存储管理删除功能

**创建时间**: 2026-03-04 16:35
**优先级**: P1

---

## 🎯 用户需求

1. **手动删除功能**：在存储管理页面可以手动删除生产文件或过程文件
2. **存储空间有限**：云存储（R2）和本地磁盘空间都有限
3. **定时删除功能**：支持更短时间内的自动清理（比现有的 7 天更灵活）

---

## 📋 需求分解

### 需求 1: 文件列表展示

**当前问题**：
- 存储管理页面只有空壳，没有实际文件列表
- 无法看到 R2 和本地存储的文件

**需求**：
- 展示 R2 各目录的文件列表（raw、processed、ready、archive）
- 展示本地 working 和 output 目录的文件
- 显示文件大小、修改时间
- 支持切换不同目录

---

### 需求 2: 手动删除功能

**需求**：
- 每个文件旁边有删除按钮
- 支持批量选择删除
- 删除前确认提示
- 删除后刷新列表

**删除范围**：
- R2 文件：raw、processed、ready、archive
- 本地文件：working、output 目录

---

### 需求 3: 定时清理配置

**当前配置**：
```yaml
tasks:
  cleanup_after_days: 7  # 固定 7 天
```

**新需求**：
- 支持按目录配置不同的清理时间
- 支持更短的清理周期（如 1 天、3 天）
- 支持手动触发清理

**配置示例**：
```yaml
storage:
  auto_cleanup:
    enabled: true
    schedule: "0 2 * * *"  # 每天凌晨 2 点
    rules:
      - path: "local:working"
        days: 1  # 工作目录 1 天清理
      - path: "local:output"
        days: 3  # 输出目录 3 天清理
      - path: "r2:raw"
        days: 7  # 原始视频 7 天清理
      - path: "r2:processed"
        days: 30  # 成品视频 30 天清理
      - path: "r2:ready"
        days: 7  # 待分发 7 天清理
```

---

### 需求 4: 存储空间监控

**需求**：
- 实时显示 R2 使用量
- 实时显示本地磁盘使用量
- 空间不足时告警

---

## 🎨 前端设计

### 文件列表界面

```
┌─────────────────────────────────────────────────┐
│ 存储管理                                         │
├─────────────────────────────────────────────────┤
│ [R2 云存储] [本地存储]                           │
│                                                  │
│ 目录: [raw ▼] [processed] [ready] [archive]     │
│                                                  │
│ ┌─────────────────────────────────────────────┐ │
│ │ ☐ 文件名          大小    修改时间    操作   │ │
│ │ ☐ video1.mp4     120MB   2h ago     [删除]  │ │
│ │ ☐ video2.mp4     85MB    5h ago     [删除]  │ │
│ │ ☐ video3.mp4     200MB   1d ago     [删除]  │ │
│ └─────────────────────────────────────────────┘ │
│                                                  │
│ [批量删除选中] [清理过期文件]                    │
└─────────────────────────────────────────────────┘
```

### 清理配置界面

```
┌─────────────────────────────────────────────────┐
│ 自动清理配置                                     │
├─────────────────────────────────────────────────┤
│ [ ] 启用自动清理                                 │
│                                                  │
│ 清理规则:                                        │
│ ┌─────────────────────────────────────────────┐ │
│ │ 本地工作目录 (working)    [1] 天后清理       │ │
│ │ 本地输出目录 (output)     [3] 天后清理       │ │
│ │ R2 原始视频 (raw)         [7] 天后清理       │ │
│ │ R2 成品视频 (processed)   [30] 天后清理      │ │
│ │ R2 待分发 (ready)         [7] 天后清理       │ │
│ └─────────────────────────────────────────────┘ │
│                                                  │
│ [保存配置] [立即执行清理]                        │
└─────────────────────────────────────────────────┘
```

---

## 🔧 后端设计

### API 端点

```python
# 文件列表
GET /api/storage/files?location=r2&path=raw
→ {
    "files": [
        {
            "name": "video1.mp4",
            "size": 125829120,
            "modified": "2026-03-04T14:30:00Z",
            "path": "raw/video1.mp4"
        }
    ],
    "total_size": 125829120
}

# 删除文件
DELETE /api/storage/files
{
    "location": "r2",  # r2 / local
    "paths": ["raw/video1.mp4", "raw/video2.mp4"]
}
→ {"success": true, "deleted": 2}

# 批量清理过期文件
POST /api/storage/cleanup
{
    "location": "r2",  # r2 / local / all
    "path": "raw",
    "days": 7
}
→ {"success": true, "deleted": 5, "freed_bytes": 524288000}

# 获取清理配置
GET /api/storage/cleanup-config
→ {
    "enabled": true,
    "rules": [...]
}

# 更新清理配置
PUT /api/storage/cleanup-config
{
    "enabled": true,
    "rules": [...]
}
→ {"success": true}
```

### 后端模块

```python
# src/core/storage.py (扩展)
class StorageManager:
    def list_files_with_details(self, r2_path: str) -> List[Dict]:
        """列出文件详情（大小、修改时间）"""

    def delete_files(self, r2_paths: List[str]) -> int:
        """批量删除文件"""

    def cleanup_old_files(self, r2_path: str, days: int) -> Dict:
        """清理过期文件"""

class LocalStorage:
    def list_files_with_details(self, path: str) -> List[Dict]:
        """列出本地文件详情"""

    def delete_files(self, paths: List[str]) -> int:
        """批量删除本地文件"""

    def cleanup_old_files(self, path: str, days: int) -> Dict:
        """清理本地过期文件"""
```

---

## 📅 实施计划

### Day 1: 文件列表功能
- [ ] 后端：扩展 StorageManager 和 LocalStorage
- [ ] 后端：实现 `/api/storage/files` 接口
- [ ] 前端：文件列表展示
- [ ] 前端：目录切换

### Day 2: 手动删除功能
- [ ] 后端：实现 `DELETE /api/storage/files` 接口
- [ ] 前端：删除按钮 + 确认对话框
- [ ] 前端：批量选择删除

### Day 3: 定时清理功能
- [ ] 后端：实现 `/api/storage/cleanup` 接口
- [ ] 后端：实现清理配置接口
- [ ] 配置：新增 `storage.auto_cleanup` 配置段
- [ ] 后端：定时任务（APScheduler）

### Day 4: 前端清理配置
- [ ] 前端：清理配置界面
- [ ] 前端：立即执行清理按钮
- [ ] 集成测试

---

## ✅ 完成标准

- [ ] 可以查看 R2 和本地文件列表
- [ ] 可以手动删除单个或多个文件
- [ ] 可以配置不同目录的清理周期
- [ ] 可以手动触发清理
- [ ] 定时任务自动清理过期文件
- [ ] 存储空间实时监控

