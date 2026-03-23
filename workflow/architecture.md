# video-factory 架构现状（代码基线）

最后更新：2026-03-22

## 1) 分层结构
- `src/core/`：配置、任务模型、任务存储、存储管理、通知、运行时心跳
- `src/production/`：下载、自管 ASR/翻译/Volcengine TTS、字幕修复、质检
- `src/factory/`：长视频、短切片、封面、元数据、图文、加工编排
- `src/creation/`：高光提取、主体检测、智能裁剪、字幕/转场/BGM 组合成片
- `src/distribute/`：发布器与调度器（含失败重试与重放）
- `workers/`：编排器主循环 + 调度器并行运行
- `src/platform_services/`：多平台抽象层 — PlatformService ABC、PlatformRegistry 单例、TokenManager（TTLCache + DB）、OAuth 异常、PublishTemplateService（发布模板管理 + 变量替换）
- `api/auth.py`：认证模块 — 用户注册/登录、bcrypt 密码哈希、itsdangerous 签名 httpOnly Cookie 会话、Token 脱敏工具
- `api/routes/`：任务、生产、加工、分发、系统、发布账号、存储、频道监控、OAuth、发布模板、Web 页面/partials
- `web/templates/`：Jinja2 页面 + HTMX partials + 独立登录/注册页 + 平台账号管理页

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
- Docker CJK 字体：Dockerfile 安装 `fonts-noto-cjk`（Noto Sans CJK SC）和 `fontconfig`，构建时执行 `fc-cache -f` 刷新字体缓存；字幕渲染（ffmpeg/libass）在 Linux 上按优先级探测 Noto Sans CJK SC → WenQuanYi Zen Hei → Arial Unicode MS
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

## 5) 多平台 OAuth 认证与发布基础设施
- 平台抽象层：`src/platform_services/base.py` 定义 `PlatformService` ABC，包含 `get_auth_url`、`handle_callback`、`refresh_token`、`check_token_status`、`publish_video` 等抽象方法
- 枚举与数据类：`PlatformType`（14 个平台）、`AuthMethod`（oauth2/cookie）、`OAuthCredential`、`PlatformAccount`、`PublishResult`
- 平台注册表：`PlatformRegistry` 单例，支持 register/get/list_platforms
- Token 管理器：`TokenManager` 使用 `cachetools.TTLCache`（maxsize=1000, ttl=30min）+ SQLite 持久化，token 剩余时间 < 600s 时自动刷新
- 自定义异常：`PlatformError`、`OAuthError`、`TokenExpiredError`、`PublishError`
- 新增数据库表：
  - `platform_accounts`：平台账号（id, user_id, platform, auth_method, platform_uid, username, nickname, avatar_url, status, cookie_path）
  - `oauth_credentials`：OAuth 凭证（account_id, platform, access_token, refresh_token, expires_at, refresh_expires_at, raw）
  - `publish_tasks_v2`：发布任务 v2（account_id, platform, title, description, tags, video_path, cover_path, status, scheduled_at, attempts）
  - `publish_templates`：发布模板（id, user_id, name, platforms, title_template, description_template, tags, platform_options, created_at, updated_at）
- OAuth 路由（`api/routes/oauth.py`，前缀 `/api/oauth`）：
  - `GET /platforms` — 已注册平台列表
  - `GET /authorize/{platform}` — 发起 OAuth 授权（302 重定向）
  - `GET /callback/{platform}` — 处理 OAuth 回调（支持用户拒绝授权）
  - `GET /accounts` — 已绑定账号列表
  - `GET /accounts/{id}` — 账号详情
  - `DELETE /accounts/{id}` — 解绑账号
- 前端页面：`/platform-accounts` 平台账号管理（Alpine.js）
- 发布模板路由（`api/routes/templates.py`，前缀 `/api/templates`）：
  - `GET /templates` — 模板列表（可选 `user_id` 过滤）
  - `POST /templates` — 创建模板
  - `GET /templates/{id}` — 模板详情
  - `PUT /templates/{id}` — 更新模板
  - `DELETE /templates/{id}` — 删除模板
  - `POST /templates/{id}/apply` — 应用模板生成任务规格（支持 `{{var}}` 变量替换）
- 批量发布端点（`api/routes/publish_v2.py`）：
  - `POST /api/publish/v2/batch` — 批量创建发布任务，接受多个 `CreatePublishRequest`
- 性能索引：
  - `idx_publish_tasks_v2_platform_status` — `publish_tasks_v2(platform, status)` 复合索引
  - `idx_publish_tasks_v2_published` — `publish_tasks_v2(status, published_at)` 复合索引
  - `idx_platform_accounts_status` — `platform_accounts(platform, status)` 复合索引
- 依赖：`cachetools>=5.3.0`

## 6) 认证系统
- 用户存储：`config/users.json`（bcrypt 哈希密码，JSON 文件）
- 会话管理：`itsdangerous.URLSafeTimedSerializer` 签名的 httpOnly Cookie（`vf_session`，30 天有效期）
- 密钥持久化：`config/.session_secret`（自动生成，或通过 `VF_SECRET_KEY` 环境变量指定）
- Bootstrap 模式：无用户时认证完全跳过（向后兼容本地开发），首次访问 `/login` 自动跳转 `/register`
- 注册控制：首个用户可直接注册，后续注册需 `VF_ALLOW_REGISTRATION=true`
- 受保护端点：
  - `system.py`：9 个敏感端点（配置读写、测试、Cookie）
  - `storage.py`：3 个写端点（删除、清理、配置更新）
  - `publish.py`：6 个写端点（创建、上传、删除、测试、设置默认）
  - `/settings` 页面（302 重定向到 `/login`）
- Token 脱敏：GET 响应中 api_key/token/access_token 等字段自动掩码（`****xxxx`）；POST 保存时自动还原未修改的密钥
- 页面路由：`/login`、`/register`（独立 HTML，不继承 base.html）
- API 路由：`/api/auth/register`、`/api/auth/login`、`/api/auth/logout`、`/api/auth/status`
- 依赖：`passlib[bcrypt]>=1.7.4`、`bcrypt>=4.0.0,<5.0.0`、`itsdangerous>=2.0`

## 6) 测试基线
- 当前仓库测试覆盖 API 合约、页面/partials、运行时健康、调度器、生产异常分型、scope 编排等。
- 基线命令：`./.venv/bin/python -m pytest -q`
- 发布/创作模块补充回归：
  - `./.venv/bin/python -m pytest -q tests/test_highlight_detector.py tests/test_cover_generator.py tests/test_factory_pipeline_creation.py tests/test_creation_review_gate.py`
  - `./.venv/bin/python -m pytest -q tests/web/test_api_contract.py tests/web/test_pages_http.py`
  - `./.venv/bin/python -m pytest -q tests/e2e/test_frontend_playwright.py`

## 7) 已识别的技术债（代码层面）
1. 凭证明文风险：
   - `config/settings.yaml`
   - `scripts/groq_whisper_proxy.py`
2. 运维依赖门槛：
   - 本地运行依赖 Python 3.11 虚拟环境；若未创建 `.venv` 或未设置 `VF_PYTHON_BIN`，服务与 E2E 不会启动。
3. 配置迁移兼容：
   - 历史 `tasks.json` 若仍保存 `klic_task_id` / `klic_progress`，加载时会自动迁移到 `translation_task_id` / `translation_progress`。
