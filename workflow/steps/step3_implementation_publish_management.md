# Step 3: 实施 - 发布管理功能

**开始时间**: 2026-03-04 17:15
**负责人**: Codex
**预计工期**: 5天

---

## 📋 实施任务清单

### Day 1: 数据模型 + 基础 API

**任务 1.1**: 创建数据模型
- 文件: `src/distribute/models.py`
- 定义 `PublishTask` 和 `Account` 数据类

**任务 1.2**: 扩展数据库
- 文件: `src/core/database.py`
- 新增表: `publish_tasks`, `accounts`
- 新增方法: `insert_publish_task()`, `get_publish_tasks()`, `insert_account()`, `get_accounts()`

**任务 1.3**: 实现任务 CRUD API
- 文件: `web/app.py`
- `POST /api/publish/tasks` - 创建任务
- `GET /api/publish/tasks` - 获取任务列表
- `DELETE /api/publish/tasks/{id}` - 删除任务

**任务 1.4**: 实现账号 CRUD API
- 文件: `web/app.py`
- `POST /api/publish/accounts` - 添加账号
- `GET /api/publish/accounts` - 获取账号列表
- `DELETE /api/publish/accounts/{id}` - 删除账号

---

### Day 2: 发布执行逻辑

**任务 2.1**: 创建 SocialAutoUploadAdapter
- 文件: `src/distribute/adapter.py`
- 实现 `publish()` 方法
- 支持 5 个平台的脚本调用
- 实现 URL 提取逻辑

**任务 2.2**: 创建 PublishManager
- 文件: `src/distribute/manager.py`
- 实现 `execute_task()` 方法
- 实现 `retry_task()` 方法
- 集成 SocialAutoUploadAdapter

**任务 2.3**: 实现执行 API
- 文件: `web/app.py`
- `POST /api/publish/tasks/{id}/execute` - 执行发布
- `POST /api/publish/tasks/{id}/retry` - 重试任务
- 使用 BackgroundTasks 异步执行

**任务 2.4**: 实现统计 API
- 文件: `web/app.py`
- `GET /api/publish/stats` - 获取统计数据

---

### Day 3: 前端任务列表

**任务 3.1**: 发布队列界面
- 文件: `web/templates/publish.html`
- 统计卡片（待发布、发布中、今日成功、失败）
- 平台筛选按钮
- 任务列表表格

**任务 3.2**: 任务状态展示
- 状态标签样式（pending/publishing/success/failed）
- 操作按钮（发布/重试/查看）
- 实时刷新逻辑

**任务 3.3**: Alpine.js 交互
- `publishApp()` 数据管理
- `loadTasks()` 加载任务
- `executeTask()` 执行发布
- `retryTask()` 重试任务

---

### Day 4: 前端任务创建

**任务 4.1**: 新建任务表单
- 文件: `web/templates/publish.html`
- 视频选择下拉框（从已完成任务加载）
- 平台选择（多选）
- 账号选择下拉框

**任务 4.2**: 表单字段
- 标题输入框
- 描述文本域
- 标签输入（动态添加）
- 封面上传
- 发布时间选择（立即/定时）

**任务 4.3**: 表单提交
- 验证必填字段
- 调用 `POST /api/publish/tasks`
- 提交后刷新列表

**任务 4.4**: 视频选择 API
- 文件: `web/app.py`
- `GET /api/tasks/completed` - 获取已完成的翻译任务

---

### Day 5: 账号管理 + 测试

**任务 5.1**: 账号管理界面
- 文件: `web/templates/publish.html` 或独立页面
- 账号列表表格
- 添加账号表单
- 删除账号按钮

**任务 5.2**: 账号测试功能
- 文件: `web/app.py`
- `POST /api/publish/accounts/{id}/test` - 测试账号
- 前端测试按钮 + 结果显示

**任务 5.3**: 配置界面
- 文件: `web/templates/settings.html`
- 新增"发布管理"配置区
- social-auto-upload 路径配置
- 账号目录配置

**任务 5.4**: 端到端测试
- 创建测试账号
- 创建发布任务
- 执行发布（使用测试视频）
- 验证状态更新
- 测试重试功能

---

## 📝 实施注意事项

1. **social-auto-upload 检查**: 启动时检查路径是否存在
2. **Cookie 格式**: 不同平台 cookie 格式可能不同，需要验证
3. **超时处理**: 发布可能耗时 5-10 分钟，设置合理超时
4. **并发限制**: 建议同时最多 2-3 个发布任务
5. **错误日志**: 记录完整的 subprocess 输出用于调试
6. **URL 解析**: 不同平台输出格式不同，需要灵活解析

---

## 🔗 参考文档

- 需求文档: `workflow/steps/requirements_publish_management.md`
- 设计文档: `workflow/steps/step2_design_publish_management.md`
- social-auto-upload: https://github.com/dreammis/social-auto-upload
- 现有代码: `src/distribute/publisher.py`

---

## ✅ 完成标准

- [ ] 数据库表创建成功
- [ ] 可以创建发布任务
- [ ] 可以选择平台和账号
- [ ] 可以配置标题、描述、标签
- [ ] 可以执行发布（调用 social-auto-upload）
- [ ] 可以查看发布状态
- [ ] 可以重试失败任务
- [ ] 可以管理平台账号
- [ ] 统计数据正确显示
- [ ] 前端界面完整可用
