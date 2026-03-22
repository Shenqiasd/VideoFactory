# video-factory 架构现状（代码基线）

最后更新：2026-03-13

## 1) 分层结构
- `src/core/`：配置、任务模型、任务存储、存储管理、通知、运行时心跳
- `src/production/`：下载、自管 ASR/翻译/Volcengine TTS、字幕修复、质检
- `src/factory/`：长视频、短切片、封面、元数据、图文、加工编排
- `src/creation/`：高光提取、主体检测、智能裁剪、字幕/转场/BGM 组合成片
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
- 页面展示口径：
  - 任务详情 / 任务列表 / dashboard 统一优先读取持久化 `task.progress`
  - 翻译阶段 UI 字段统一为 `translation_task_id` / `translation_progress`
  - 失败任务阶段展示依赖 `task.timeline` 回溯最近一个非失败步骤

任务范围 `task_scope`：
- `subtitle_only`：翻译后进入加工，生成“有字幕长视频、无配音发布链路”
- `subtitle_dub`：翻译配音 + 质检后直接完成
- `dub_and_copy`：翻译配音 + 加工后完成（跳过发布）
- `full`：全流程到发布

创作配置与创作状态：
- 创建页会在 `scope=dub_and_copy/full` 下展开“创作配置”面板，配置项统一写入 `creation_config`
- 任务详情页通过 `GET /api/tasks/{task_id}/creation-summary` 读取前端友好的创作摘要，展示高光片段、裁剪策略、平台变体、封面与审核动作
- 创作审核沿用 `creation_status.review_required / review_status` 作为发布门禁事实来源
- 详情页审核操作调用 `/api/factory/review/approve` 与 `/api/factory/review/reject`，审核通过后发布链路可继续推进
- 任务列表/最近完成列表会展示轻量 creation badges（切片数量 / 待审核 / 已出封面）

创作策略现状：
- `highlight_strategy=hybrid`：字幕语义 + scene/audio 混合打分
- `highlight_strategy=semantic`：仅按语义分数选段，不叠加 scene/audio
- `highlight_strategy=legacy`：直接走旧 `ShortClipExtractor` 回退逻辑
- 封面生成现支持 horizontal + vertical 两类输出，优先使用 YouTube 原始缩略图，失败时回退关键帧抽取

## 3) 服务拓扑（本机）
- API：默认 `9000`（本地），Railway 等 PaaS 通过 `PORT` 环境变量动态分配
- Groq Whisper 代理：`8866`
- Edge-TTS 代理：`8877`
- Worker：后台循环（心跳写入 `~/.video-factory/worker_heartbeat.json`）
- 下载运行时：`yt-dlp` 作为 Python 依赖安装到项目 `.venv`，下载命令优先解析当前运行时目录下的 `yt-dlp`
- 源视频上传：R2 / `rclone` 仅用于增强型回传；本地自管链路在上传失败时继续使用 `source_local_path`
- Railway 部署：`railway.toml` 配置启动命令、健康检查路径 `/api/health`（30s 超时）、失败重启策略；API 端口优先级 `VF_API_PORT` → `PORT` → `9000`，绑定地址优先级 `VF_API_HOST` → `HOST` → `0.0.0.0`
- YouTube ASR：优先 `youtube-transcript-api`，为空时回退 `yt-dlp` 自动字幕，再继续 Volcengine / Whisper 降级
- 自动字幕规范化：`yt-dlp` 回退优先读取 `srv3` 并清洗滚动重复 cue，避免上游字幕换行滚动导致逐行翻译重复
- 字幕主翻译：在 `ProductionPipeline` 中先用 `SentenceRegrouper` 将连续碎片 cue 合并成 sentence group，再翻译并投影回原 cue 数量，降低逐行碎片翻译的语义断裂
- 字幕补翻：若火山逐行翻译后仍残留纯英文碎片句，`SubtitleRepairer` 会带上前后文做二次定点补翻，再写回 `target_language_srt.srt` / `bilingual_srt.srt`
- 质检规则：除中文覆盖率、未翻译占比外，额外拦截纯英文残留行，避免字幕仍有英文漏翻却拿到 `qc_score=100`

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
  - 任务详情页显示账号绑定、发布事件流，以及创作结果/创作审核操作
  - 新建任务页会前置校验平台账号是否可用，并支持创作配置提交

## 5) 测试基线
- 当前仓库测试覆盖 API 合约、页面/partials、运行时健康、调度器、生产异常分型、scope 编排等。
- 基线命令：`./.venv/bin/python -m pytest -q`
- 发布/创作模块补充回归：
  - `./.venv/bin/python -m pytest -q tests/test_highlight_detector.py tests/test_cover_generator.py tests/test_factory_pipeline_creation.py tests/test_creation_review_gate.py`
  - `./.venv/bin/python -m pytest -q tests/web/test_api_contract.py tests/web/test_pages_http.py`
  - `./.venv/bin/python -m pytest -q tests/e2e/test_frontend_playwright.py`

## 6) 已识别的技术债（代码层面）
1. 凭证明文风险：
   - `config/settings.yaml`
   - `scripts/groq_whisper_proxy.py`
2. 运维依赖门槛：
   - 本地运行依赖 Python 3.11 虚拟环境；若未创建 `.venv` 或未设置 `VF_PYTHON_BIN`，服务与 E2E 不会启动。
3. 配置迁移兼容：
   - 历史 `tasks.json` 若仍保存 `klic_task_id` / `klic_progress`，加载时会自动迁移到 `translation_task_id` / `translation_progress`。
