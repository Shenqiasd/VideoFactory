# task-012: 新建任务表单

新建任务表单添加视频/平台/账号选择下拉框 [REMEMBER] 使用Alpine.js实现动态数据加载，平台选择后自动获取对应账号 [DECISION] 仅在scope=full时显示这些字段，保持表单简洁 [ARCHITECTURE] 数据流：completedTasks从/api/tasks/completed加载，accounts根据选中平台从/api/accounts?platform=X动态加载
