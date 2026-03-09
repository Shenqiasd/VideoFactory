# task-006: 创建 PublishManager

实现 PublishManager.execute_task() 和 retry_task() 方法 [REMEMBER] PublishManager 新增两个方法：execute_task() 从 TaskStore 加载任务并调用 publish_to_all() 执行发布，retry_task() 用于重试失败的发布任务 [DECISION] execute_task() 支持可选的 platforms 参数来过滤发布平台，retry_task() 内部复用 execute_task() 逻辑以保持代码简洁 [ARCHITECTURE] PublishManager 依赖 TaskStore 获取任务数据，通过 publish_to_all() 统一处理多平台发布逻辑
