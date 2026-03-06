# Step 3: 实施 - 存储管理删除功能

**开始时间**: 2026-03-04 16:50
**负责人**: Codex
**预计工期**: 4天

---

## 📋 实施任务清单

### Day 1: 文件列表功能

**任务 1.1**: 扩展 StorageManager
- 文件: `src/core/storage.py`
- 新增方法: `list_files_with_details(r2_path: str)`
- 使用 `rclone lsjson` 获取详细信息
- 返回: 文件名、大小、修改时间

**任务 1.2**: 扩展 LocalStorage
- 文件: `src/core/storage.py`
- 新增方法: `list_files_with_details(path: str)`
- 遍历本地目录获取文件信息

**任务 1.3**: 实现文件列表 API
- 文件: `web/app.py`
- 新增: `GET /api/storage/files`
- 参数: location (r2/local), path
- 返回: 文件列表 + 总大小

**任务 1.4**: 前端文件列表
- 文件: `web/templates/storage.html`
- 使用 Alpine.js 实现动态加载
- 目录切换按钮
- 文件列表表格

---

### Day 2: 删除功能

**任务 2.1**: 扩展 StorageManager
- 文件: `src/core/storage.py`
- 新增方法: `delete_files(r2_paths: List[str])`
- 批量删除 R2 文件

**任务 2.2**: 扩展 LocalStorage
- 文件: `src/core/storage.py`
- 新增方法: `delete_files(paths: List[str])`
- 批量删除本地文件

**任务 2.3**: 实现删除 API
- 文件: `web/app.py`
- 新增: `DELETE /api/storage/files`
- Body: location, paths
- 返回: 删除数量

**任务 2.4**: 前端删除功能
- 文件: `web/templates/storage.html`
- 单个文件删除按钮
- 批量选择 + 批量删除
- 删除确认对话框

---

### Day 3: 清理功能

**任务 3.1**: 实现清理方法
- 文件: `src/core/storage.py`
- StorageManager: `cleanup_old_files(r2_path: str, days: int)`
- LocalStorage: `cleanup_old_files(path: str, days: int)`
- 返回: 删除数量 + 释放空间

**任务 3.2**: 实现清理 API
- 文件: `web/app.py`
- 新增: `POST /api/storage/cleanup`
- Body: location, path, days
- 返回: 删除数量 + 释放空间

**任务 3.3**: 前端清理按钮
- 文件: `web/templates/storage.html`
- "清理过期文件" 按钮
- 输入天数对话框
- 显示清理结果

---

### Day 4: 定时任务 + 配置

**任务 4.1**: 创建定时任务模块
- 文件: `src/core/scheduler.py`
- 使用 APScheduler
- 定时执行清理任务

**任务 4.2**: 实现清理配置 API
- 文件: `web/app.py`
- 新增: `GET /api/storage/cleanup-config`
- 新增: `PUT /api/storage/cleanup-config`

**任务 4.3**: 配置文件
- 文件: `config/settings.yaml`
- 新增 `storage.auto_cleanup` 配置段

**任务 4.4**: 前端配置界面
- 文件: `web/templates/settings.html`
- 新增 "存储" 配置标签
- 清理规则配置表单
- 保存配置按钮

**任务 4.5**: 集成测试
- 测试文件列表
- 测试删除功能
- 测试清理功能
- 测试定时任务

---

## 📝 实施注意事项

1. **rclone 命令**: 使用 `lsjson` 获取详细信息，`delete` 删除文件
2. **时间计算**: 使用文件修改时间判断是否过期
3. **安全性**: 删除前确认，避免误删
4. **性能**: 大量文件时分页加载
5. **错误处理**: 删除失败时返回详细错误信息

---

## 🔗 参考文档

- 需求文档: `workflow/steps/requirements_storage_management.md`
- 设计文档: `workflow/steps/step2_design_storage_management.md`
- 现有代码: `src/core/storage.py`

---

## ✅ 完成标准

- [ ] 可以查看 R2 和本地文件列表
- [ ] 可以手动删除单个或多个文件
- [ ] 可以手动触发清理过期文件
- [ ] 可以配置不同目录的清理周期
- [ ] 定时任务自动清理
- [ ] 前端界面完整可用

