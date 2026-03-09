# video-factory 架构现状（代码基线）

最后更新：2026-03-09

## 1) 分层结构
- `src/core/`：配置、任务模型、任务存储、存储管理、通知、运行时心跳
- `src/production/`：下载、KlicStudio 提交/轮询/产物下载、质检
- `src/factory/`：长视频、短切片、封面、元数据、图文、加工编排
- `src/distribute/`：发布器与调度器（含失败重试与重放）
- `workers/`：编排器主循环 + 调度器并行运行
- `api/routes/`：任务、生产、加工、分发、系统、Web 页面/partials
- `web/templates/`：Jinja2 页面 + HTMX partials

## 2) 核心任务流
任务状态机定义在 `src/core/task.py`：
- 主状态：`queued -> downloading -> downloaded -> uploading_source -> translating -> qc_checking -> qc_passed -> processing -> uploading_products -> ready_to_publish -> publishing -> completed`
- 发布异常分支：`failed`
- 发布部分成功分支：`partial_success`
- 当前发布阶段支持按 `job_id` 做取消、重试、人工确认成功/失败。

任务范围 `task_scope`：
- `subtitle_only`：翻译后进入加工，生成“有字幕长视频、无配音发布链路”
- `subtitle_dub`：翻译配音 + 质检后直接完成
- `dub_and_copy`：翻译配音 + 加工后完成（跳过发布）
- `full`：全流程到发布

## 3) 服务拓扑（本机）
- API：`9000`
- KlicStudio：`8888`
- Groq Whisper 代理：`8866`
- Edge-TTS 代理：`8877`
- Worker：后台循环（心跳写入 `~/.video-factory/worker_heartbeat.json`）

## 4) 当前发布系统实现现状
- 发布入口：
  - API：`api/routes/distribute.py`
  - 账号管理：`api/routes/publish.py`
  - 执行器：`src/distribute/publisher.py`
  - 调度器：`src/distribute/scheduler.py`
- 持久化：
  - 发布队列：SQLite `publish_jobs`
  - 发布审计：SQLite `publish_job_events`
  - 账号：SQLite `accounts`
- 账号能力模型：
  - 字段：`is_default`、`capabilities_json`、`last_error`
  - 校验项：平台是否支持、Cookie 是否存在、是否可自动发布
- 账号绑定策略：
  - 任务可持久化 `publish_accounts: {platform -> account_id}`
  - 执行时优先用任务绑定账号，否则使用平台默认账号
  - 账号与平台不匹配会被 API 和执行器双重拒绝
- 发布作业模型：
  - `job_id` 为 UI/API 操作主键
  - `idempotency_key` 为去重键
  - 作业状态：`pending | publishing | manual_pending | done | failed | cancelled`
- 人工发布链路：
  - 自动发布器返回 `manual_checklist` 时进入 `manual_pending`
  - 支持 `/api/distribute/manual/complete` 与 `/api/distribute/manual/fail`
  - 任务状态会根据全局结果进入 `completed / partial_success / failed`
- 页面可观测性：
  - 发布管理页显示队列、账号状态、最近事件
  - 任务详情页显示账号绑定和该任务的发布事件流
  - 新建任务页会前置校验平台账号是否可用

## 5) 测试基线
- 当前仓库测试覆盖 API 合约、页面/partials、运行时健康、调度器、生产异常分型、scope 编排等。
- 基线命令：`python3.11 -m pytest -q`
- 发布模块补充回归：
  - `python3.11 -m pytest -q tests/test_publish_scheduler.py`
  - `python3.11 -m pytest -q tests/web/test_api_contract.py`
  - `python3.11 -m pytest -q tests/e2e/test_frontend_playwright.py -k 'accounts_page_can_create_and_validate_account or publish_page_supports_cancel_retry_manual_and_partial_recovery'`

## 6) 已识别的技术债（代码层面）
1. 凭证明文风险：
   - `config/settings.yaml`
   - `scripts/groq_whisper_proxy.py`
2. 页面数据一致性风险：
   - `api/routes/pages.py` 的 `recent_completed_partial` 对时间字段按 ISO 解析，但任务时间戳是 float。
   - `api/routes/pages.py` 使用 `target_platforms` 字段做过滤/展示，任务模型默认未持久化该字段。
3. 模板兼容性风险：
   - `api/routes/pages.py` 仍使用旧版 `TemplateResponse(name, context)` 调用顺序，测试中存在 deprecation warning。
4. 运维可移植性风险：
   - `scripts/start_all.sh` 中 KlicStudio 路径和 ffmpeg-full 路径写死为本机路径。
