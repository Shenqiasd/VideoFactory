# task-004: 实现账号 CRUD API

实现账号 CRUD API [REMEMBER] 使用 JSON 文件存储账号数据 (data/accounts.json) [DECISION] 采用原子写入 (tmp + replace) 防止数据损坏 [ARCHITECTURE] AccountStore 负责持久化，publish router 提供 REST 接口
