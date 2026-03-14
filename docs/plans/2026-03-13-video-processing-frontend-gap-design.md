# Video Processing Frontend Gap Design

**目标**

- 让前端明确体现 VideoFactory 已有的视频加工能力，而不是仅暴露“任务范围”和“生成切片”开关。
- 一次补齐创建、查看、审核、结果解释四个关键前端缺口，使高光、智能裁剪、平台适配、创作审核对用户可见且可操作。
- 在尽量复用现有 `creation_config`、`creation_state`、`creation_status`、`products` 数据结构的前提下完成补齐，避免重做后端任务模型。

**现状问题**

- 创建页 `web/templates/new_task.html` 仅支持：任务范围、字幕样式、是否生成短视频/图文。
- 后端虽已支持 `creation_config`，但 Web 表单仍主要走兼容接口 `/api/tasks/create`，无法提交完整创作参数。
- 任务详情页 `web/templates/task_detail.html` 只展示通用任务信息和产物下载，未展示高光片段、裁剪结果、fallback、审核状态。
- 后端已暴露加工审核接口 `/api/factory/review/approve`、`/api/factory/review/reject`，但前端没有任何入口。
- 用户能下载文件，但不能理解“系统选了哪些高光”“是否真的用了智能裁剪”“为什么现在不能发布”。

**设计原则**

- 保留现有 `scope` 作为快捷模板，不推翻当前任务创建心智。
- 以 `creation_config` 为唯一创作输入源，避免前端再发明第二套配置字段。
- 以 `creation_state` / `creation_status` 为唯一创作过程与审核状态源，避免页面自己猜状态。
- 以 `products` 为最终产物事实来源，但对前端增加聚合视图，减少模板层的重复拼装。
- “能力可见”优先于“高级预览”；先让用户看见和理解结果，再考虑媒体级预览增强。

**方案选择**

### 方案 A：创建页补几个开关

- 在现有表单上增加高光、裁剪、BGM 等字段。
- 优点：改动快。
- 缺点：详情页仍无法解释创作结果，也无法补审核闭环。

### 方案 B：创建页 + 详情页双补齐（推荐）

- 创建页补创作配置面板。
- 详情页补创作结果与审核中心。
- 新增面向前端的创作摘要接口或在详情接口内统一输出可消费结构。
- 优点：一次解决“不能配、不能看、不能审、不能解释”四类问题。
- 缺点：前端改动面较大，但结构最稳。

### 方案 C：独立创作工作台

- 单独新增 `/tasks/{id}/creation` 页面承载创作配置、结果和审核。
- 优点：扩展空间最大。
- 缺点：引入新的产品入口和学习成本，短期收益不如方案 B。

**推荐**：方案 B。当前项目已经有任务创建页、任务详情页、发布页三段式流程，补齐这两页即可自然承接现有使用路径。

**目标能力映射**

- **高光提取**
  - 前端新增配置：片段数、片段最短时长、片段最长时长。
  - 前端新增结果：高光片段标题、起止时间、时长、得分摘要。
  - 注意：`highlight_strategy` 当前只有配置字段，尚未真正驱动执行分支，因此本次设计中先不把它作为面向用户的主能力售卖；待后端接通后再升级。

- **智能裁剪**
  - 前端新增配置：`crop_mode`（`smart` / `center`）。
  - 前端新增结果：裁剪策略、焦点类别、是否退化为中心裁剪。
  - 前端文案需明确：智能裁剪依赖环境能力，若缺少依赖会自动退化。

- **平台适配**
  - 前端新增配置：目标平台多选（抖音 / 小红书 / B站）。
  - 前端新增结果：按平台展示生成出的短视频变体。

- **背景音乐 / 片头片尾 / 转场**
  - 前端新增配置：BGM 路径、BGM 音量、片头路径、片尾路径、转场类型、转场时长。
  - 前端结果页展示：生成配置摘要，不做媒体级试听预览。

- **创作审核**
  - 前端新增结果与操作：查看当前审核状态，执行通过/拒绝。
  - 审核结果需直接联动详情页、任务列表和发布阻塞提示。

**页面设计**

### 1. 创建页：基础配置 + 创作配置

保留现有基础结构，在 `web/templates/new_task.html` 中追加“创作配置”折叠区：

- 基础信息：URL、源语言、目标语言、scope。
- 字幕样式：保留现有配置和预览。
- 创作配置：
  - 片段数 `clip_count`
  - 最短时长 `duration_min`
  - 最长时长 `duration_max`
  - 裁剪模式 `crop_mode`
  - 审核模式 `review_mode`
  - 目标平台 `platforms`
  - BGM `bgm_path` / `bgm_volume`
  - 片头片尾 `intro_path` / `outro_path`
  - 转场 `transition` / `transition_duration`
- 产出摘要卡：
  - 预计生成多少条短视频
  - 面向哪些平台
  - 是否启用智能裁剪
  - 是否需要审核

交互要求：

- `scope=subtitle_only/subtitle_dub` 时创作配置默认折叠并禁用大部分字段。
- `scope=dub_and_copy/full` 时创作配置默认展开。
- 页面仍支持最小路径创建任务，但一旦展开创作配置则应提交完整 `creation_config`。

### 2. 详情页：创作结果与审核中心

在 `web/templates/task_detail.html` 新增“创作结果”区块：

- **创作配置摘要**：展示本任务提交时的关键配置。
- **创作执行状态**：展示 `stage`、`segments_total`、`variants_total`、`used_fallback`、`warnings`。
- **高光片段列表**：展示 `selected_segments` 中每个片段的标题、时间、时长、得分、裁剪策略。
- **短视频变体列表**：按 segment 聚合并列出平台 variant。
- **创作审核操作**：当 `review_status=pending` 时提供 approve/reject 按钮。

交互要求：

- `review_status=approved` 时显示“已通过，可进入发布”。
- `review_status=rejected` 时显示拒绝原因及建议。
- `used_fallback=true` 时显示显著提示，解释本次为回退策略生成。

### 3. 任务列表 / 最近完成列表：增加创作能力徽标

在列表级只显示高价值摘要：

- `高光 xN`
- `智能裁剪`
- `待审核`
- `已出封面`

目标是让用户一眼看出“这个任务做了什么创作加工”，而不是把详情页内容搬到列表。

**接口设计**

### 创建任务

- 保留：`POST /api/tasks/create` 作为兼容表单入口。
- 新主入口：`POST /api/tasks/` 提交 JSON，直接写入 `creation_config`。

前端策略：

- 新建页改为优先提交 JSON 到 `/api/tasks/`。
- 旧兼容表单和已有自动化继续可用，不强制移除。

### 创作摘要接口

建议新增：`GET /api/tasks/{task_id}/creation-summary`

返回结构：

- `config`
- `status`
- `segments`
- `variants_by_segment`
- `covers`
- `actions`

这样前端详情页无需在模板层深度遍历 `products` + `creation_state` 来重建关系，可降低模板脚本复杂度。

### 创作审核接口

- 复用现有：
  - `POST /api/factory/review/approve`
  - `POST /api/factory/review/reject`

无需新增接口，但要在详情页真正接入并处理成功/失败提示。

**后端配套补强**

- 让 `highlight_strategy` 真正进入执行分支，否则前端不应把它作为主配置项开放。
- 让 `src/factory/cover.py` 的 vertical 封面真正接通，否则前端平台封面展示会不一致。
- 对 `creation-summary` 做后端聚合，避免前端靠字符串和文件名猜 segment/variant 关系。

**兼容策略**

- 不移除现有 `/api/tasks/create`、`/api/tasks/batch-create`，避免破坏旧表单和自动化调用。
- `scope` 仍保留，作为创作配置的快捷模板和默认值来源。
- 若任务没有短视频产物，不展示审核按钮。
- 若缺少 YOLO/cv2 等可选依赖，前端明确显示“本次使用中心裁剪回退”，不再静默失败。

**测试策略**

- API 合同测试：
  - 创建任务提交 `creation_config` 后落库正确。
  - `creation-summary` 结构稳定。
  - 审核 approve/reject 后状态正确变化。
- 页面测试：
  - 创建页渲染创作配置字段。
  - 详情页渲染高光片段、裁剪模式、审核操作。
- E2E 测试：
  - 创建带创作配置的任务。
  - 详情页能看见创作结果。
  - 审核通过后发布不再被 review gate 阻塞。

**验收标准**

- 创建页能提交完整创作配置。
- 详情页能解释高光、裁剪、变体、审核状态。
- 前端能执行创作审核通过/拒绝。
- 列表页能体现任务是否做了视频加工。
- `highlight_strategy` 不再是摆设，或在 UI 中暂不暴露。
- 竖版封面补通后，详情页和列表可展示平台相关封面。

**影响范围**

- `web/templates/new_task.html`
- `web/templates/task_detail.html`
- `web/templates/partials/task_list.html`
- `web/templates/partials/recent_completed.html`
- `api/routes/tasks.py`
- `api/routes/factory.py`
- 可能新增创作摘要路由
- `src/creation/pipeline.py`
- `src/factory/cover.py`
- 相关 API / 页面 / E2E 测试文件
