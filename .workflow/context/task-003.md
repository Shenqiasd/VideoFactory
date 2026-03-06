# task-003: 实现任务 CRUD API

实现发布任务CRUD API [REMEMBER] 项目使用FastAPI+PublishScheduler管理发布队列 [DECISION] 复用现有distribute.scheduler模块而非重新实现队列逻辑 [ARCHITECTURE] API层通过全局单例访问TaskStore和PublishScheduler
