# 需求分解 - 发布管理功能

**创建时间**: 2026-03-04 17:00
**优先级**: P1

---

## 🎯 背景

当前发布管理页面是空壳，完全没有实现。需要集成 `social-auto-upload` 实现多平台视频发布。

**现有代码**：
- `src/distribute/publisher.py`：已有基础框架，但未完整实现
- `web/templates/publish.html`：前端空壳
- 配置：`distribute.social_auto_upload_path` 和 `distribute.account_dir`

---

## 📋 核心需求

### 需求 1: 发布任务管理

**功能**：
- 创建发布任务（选择视频、平台、账号）
- 查看发布队列（待发布、发布中、已完成、失败）
- 编辑发布任务（标题、描述、标签、封面）
- 删除/取消发布任务
- 重试失败任务

**支持平台**：
- 抖音 (douyin)
- B站 (bilibili)
- 小红书 (xiaohongshu)
- YouTube (youtube)
- 视频号 (weixin)

---

### 需求 2: 账号管理

**功能**：
- 添加平台账号（配置 cookie）
- 查看账号列表
- 测试账号可用性
- 删除账号

**账号存储**：
- 使用 `social-auto-upload` 的账号目录结构
- 每个平台一个子目录，存储 cookie 文件

---

### 需求 3: 发布配置

**每个发布任务需要配置**：
- 视频文件路径
- 标题（必填）
- 描述/文案（可选）
- 标签/话题（可选）
- 封面图片（可选）
- 发布时间（立即/定时）
- 可见性（公开/私密/仅粉丝）

**平台特定配置**：
- 抖音：话题、@用户、位置
- B站：分区、标签
- 小红书：话题、位置
- YouTube：分类、隐私设置
- 视频号：话题

---

### 需求 4: 发布执行

**执行方式**：
- 调用 `social-auto-upload` Python 脚本
- 传递配置 JSON 文件
- 捕获输出和错误
- 提取发布 URL

**执行流程**：
```
1. 检查 social-auto-upload 是否安装
2. 检查账号 cookie 是否存在
3. 生成配置 JSON
4. 调用平台脚本（如 upload_video_to_douyin.py）
5. 解析输出，提取 URL
6. 更新任务状态
```

---

### 需求 5: 状态监控

**任务状态**：
- `pending`: 待发布
- `publishing`: 发布中
- `success`: 发布成功
- `failed`: 发布失败
- `cancelled`: 已取消

**监控指标**：
- 待发布数量
- 发布中数量
- 今日成功数量
- 失败数量
- 成功率

---

## 🎨 前端设计

### 发布队列界面

```
┌─────────────────────────────────────────────────┐
│ 发布管理                                         │
├─────────────────────────────────────────────────┤
│ [待发布: 5] [发布中: 2] [今日成功: 10] [失败: 1] │
│                                                  │
│ [全部] [B站] [抖音] [小红书] [YouTube] [视频号]  │
│                                                  │
│ [+ 新建发布任务]                                 │
│                                                  │
│ ┌─────────────────────────────────────────────┐ │
│ │ 任务      平台  类型  计划时间  状态  操作   │ │
│ │ video1   抖音  视频  立即发布  待发布 [发布] │ │
│ │ video2   B站   视频  18:00    发布中 [取消] │ │
│ │ video3   小红书 视频  已发布   成功   [查看] │ │
│ └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

### 新建发布任务界面

```
┌─────────────────────────────────────────────────┐
│ 新建发布任务                                     │
├─────────────────────────────────────────────────┤
│ 选择视频: [下拉选择已完成的任务]                 │
│                                                  │
│ 选择平台: [ ] 抖音 [ ] B站 [ ] 小红书            │
│           [ ] YouTube [ ] 视频号                 │
│                                                  │
│ 选择账号: [下拉选择已配置的账号]                 │
│                                                  │
│ 标题: [___________________________________]      │
│                                                  │
│ 描述/文案:                                       │
│ [_________________________________________]      │
│ [_________________________________________]      │
│                                                  │
│ 标签: [tag1] [tag2] [+ 添加标签]                │
│                                                  │
│ 封面: [上传图片] 或 [使用视频首帧]               │
│                                                  │
│ 发布时间: ( ) 立即发布 ( ) 定时发布 [选择时间]   │
│                                                  │
│ [取消] [保存草稿] [立即发布]                     │
└─────────────────────────────────────────────────┘
```

### 账号管理界面

```
┌─────────────────────────────────────────────────┐
│ 账号管理                                         │
├─────────────────────────────────────────────────┤
│ [+ 添加账号]                                     │
│                                                  │
│ ┌─────────────────────────────────────────────┐ │
│ │ 平台    账号名称    状态      操作           │ │
│ │ 抖音    账号1      ✓ 正常    [测试] [删除]  │ │
│ │ B站     账号2      ✗ 失效    [测试] [删除]  │ │
│ │ 小红书  账号3      ✓ 正常    [测试] [删除]  │ │
│ └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

---

## 🔧 后端设计

### API 端点

```python
# 发布任务
POST   /api/publish/tasks          # 创建发布任务
GET    /api/publish/tasks          # 获取任务列表
GET    /api/publish/tasks/{id}     # 获取任务详情
PUT    /api/publish/tasks/{id}     # 更新任务
DELETE /api/publish/tasks/{id}     # 删除任务
POST   /api/publish/tasks/{id}/execute  # 执行发布
POST   /api/publish/tasks/{id}/retry    # 重试失败任务

# 账号管理
POST   /api/publish/accounts       # 添加账号
GET    /api/publish/accounts       # 获取账号列表
DELETE /api/publish/accounts/{id}  # 删除账号
POST   /api/publish/accounts/{id}/test  # 测试账号

# 统计
GET    /api/publish/stats          # 获取统计数据
```

---

## 📊 数据模型

### PublishTask

```python
{
    "id": "pub_001",
    "task_id": "task_001",  # 关联的翻译任务
    "video_path": "/path/to/video.mp4",
    "platform": "douyin",
    "account_id": "acc_001",
    "title": "视频标题",
    "description": "视频描述",
    "tags": ["tag1", "tag2"],
    "cover_path": "/path/to/cover.jpg",
    "publish_time": "2026-03-04T18:00:00Z",  # null 表示立即
    "status": "pending",  # pending/publishing/success/failed/cancelled
    "publish_url": "",  # 发布后的 URL
    "error": "",  # 错误信息
    "created_at": "2026-03-04T10:00:00Z",
    "updated_at": "2026-03-04T10:00:00Z"
}
```

### Account

```python
{
    "id": "acc_001",
    "platform": "douyin",
    "name": "账号1",
    "cookie_path": "/path/to/cookie.json",
    "status": "active",  # active/inactive
    "last_test": "2026-03-04T10:00:00Z",
    "created_at": "2026-03-04T10:00:00Z"
}
```

---

## 🔗 social-auto-upload 集成

### 安装

```bash
git clone https://github.com/dreammis/social-auto-upload.git
cd social-auto-upload
pip install -r requirements.txt
```

### 配置

```yaml
# config/settings.yaml
distribute:
  auto_publish: false
  social_auto_upload_path: "/path/to/social-auto-upload"
  account_dir: "/path/to/accounts"
  publish_max_retries: 2
  retry_backoff_seconds: 60
```

### 调用示例

```python
# 抖音发布
config = {
    "title": "视频标题",
    "file_path": "/path/to/video.mp4",
    "tags": ["tag1", "tag2"],
    "publish_date": "2026-03-04 18:00:00",
    "account_file": "/path/to/accounts/douyin/account1.json"
}

cmd = [
    "python3",
    "/path/to/social-auto-upload/upload_video_to_douyin.py",
    "--config", "/tmp/config.json"
]
```

---

## 📅 实施计划

### Day 1: 数据模型 + 基础 API
- [ ] 创建 PublishTask 和 Account 数据模型
- [ ] 实现任务 CRUD API
- [ ] 实现账号 CRUD API

### Day 2: 发布执行逻辑
- [ ] 完善 SocialAutoUploadAdapter
- [ ] 实现发布执行接口
- [ ] 实现重试逻辑

### Day 3: 前端任务列表
- [ ] 发布队列界面
- [ ] 任务状态展示
- [ ] 平台筛选

### Day 4: 前端任务创建
- [ ] 新建任务表单
- [ ] 视频选择
- [ ] 平台和账号选择

### Day 5: 账号管理 + 测试
- [ ] 账号管理界面
- [ ] 账号测试功能
- [ ] 端到端测试

---

## ✅ 完成标准

- [ ] 可以创建发布任务
- [ ] 可以选择平台和账号
- [ ] 可以配置标题、描述、标签
- [ ] 可以执行发布（调用 social-auto-upload）
- [ ] 可以查看发布状态
- [ ] 可以重试失败任务
- [ ] 可以管理平台账号
- [ ] 统计数据正确显示

