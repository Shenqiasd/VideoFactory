# task-002: 扩展数据库

扩展数据库完成：新增 publish_tasks 和 accounts 表及 CRUD 方法 [REMEMBER] 使用 SQLite 存储发布任务和账号信息，支持多平台发布管理 [DECISION] 选择 SQLite 作为轻量级数据库方案，使用参数化查询防止 SQL 注入 [ARCHITECTURE] Database 类封装所有数据库操作，自动初始化表结构，提供完整的 CRUD 接口
