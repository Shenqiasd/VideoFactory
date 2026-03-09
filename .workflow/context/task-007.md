# task-007: 实现执行 API

实现执行API：添加execute和retry端点 [REMEMBER] 使用BackgroundTasks异步执行发布任务，复用_run_due_jobs辅助函数 [DECISION] execute端点过滤pending+due的jobs，retry端点调用scheduler.replay_failed()重置失败任务 [ARCHITECTURE] 两个端点都通过BackgroundTasks调用_run_due_jobs实现异步执行，保持代码简洁
