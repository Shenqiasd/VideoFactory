# task-015: 视频选择 API

实现视频选择API端点 GET /api/tasks/completed [REMEMBER] 使用 TaskStore.list_by_state(TaskState.COMPLETED) 查询已完成任务 [DECISION] 复用现有 TaskResponse 模型，按 created_at 倒序返回
