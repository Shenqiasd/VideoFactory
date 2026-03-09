1. [backend] 创建数据模型
   文件: src/distribute/models.py - 定义 PublishTask 和 Account 数据类
2. [backend] 扩展数据库
   文件: src/core/database.py - 新增表 publish_tasks, accounts 及相关方法
3. [backend] 实现任务 CRUD API
   文件: web/app.py - POST/GET/DELETE /api/publish/tasks
4. [backend] 实现账号 CRUD API
   文件: web/app.py - POST/GET/DELETE /api/publish/accounts
5. [backend] 创建 SocialAutoUploadAdapter
   文件: src/distribute/adapter.py - 实现 publish() 方法，支持 5 个平台
6. [backend] 创建 PublishManager
   文件: src/distribute/manager.py - 实现 execute_task() 和 retry_task() 方法
7. [backend] 实现执行 API
   文件: web/app.py - POST /api/publish/tasks/{id}/execute 和 retry，使用 BackgroundTasks
8. [backend] 实现统计 API
   文件: web/app.py - GET /api/publish/stats
9. [frontend] 发布队列界面
   文件: web/templates/publish.html - 统计卡片、平台筛选、任务列表
10. [frontend] 任务状态展示 (deps: 9)
    状态标签样式、操作按钮、实时刷新逻辑
11. [frontend] Alpine.js 交互 (deps: 9)
    publishApp() 数据管理、loadTasks()、executeTask()、retryTask()
12. [frontend] 新建任务表单 (deps: 9)
    视频选择、平台选择、账号选择下拉框
13. [frontend] 表单字段 (deps: 12)
    标题、描述、标签、封面、发布时间
14. [frontend] 表单提交 (deps: 12, 13)
    验证、调用 API、刷新列表
15. [backend] 视频选择 API
    文件: web/app.py - GET /api/tasks/completed
16. [frontend] 账号管理界面
    账号列表、添加表单、删除按钮
17. [backend] 账号测试功能
    文件: web/app.py - POST /api/publish/accounts/{id}/test
18. [frontend] 配置界面
    文件: web/templates/settings.html - 发布管理配置区
19. [general] 端到端测试 (deps: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18)
    创建测试账号、任务、执行发布、验证状态、测试重试
