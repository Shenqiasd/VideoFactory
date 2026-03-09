# task-001: 创建数据模型

创建数据模型完成 [REMEMBER] 定义了 PublishTask 和 Account 两个 dataclass，使用 Python dataclasses 实现轻量级数据结构 [DECISION] 选择 dataclass 而非普通类，因为自动生成 __init__、__repr__ 等方法，代码更简洁 [ARCHITECTURE] PublishTask 包含发布任务核心字段（task_id、platform、video_path、title 等），Account 管理平台账号信息（platform、account_id、cookies_path 等）
