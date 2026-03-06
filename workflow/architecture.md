# video-factory 架构现状（代码基线）

最后更新：2026-03-03

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
- 状态：`queued -> downloading -> downloaded -> uploading_source -> translating -> qc_checking -> qc_passed -> processing -> uploading_products -> ready_to_publish -> publishing -> completed`
- 异常分支：`failed`，支持部分重试/取消。

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

## 4) 测试基线
- 当前仓库测试覆盖 API 合约、页面/partials、运行时健康、调度器、生产异常分型、scope 编排等。
- 基线命令：`python3.11 -m pytest -q`

## 5) 已识别的技术债（代码层面）
1. 凭证明文风险：
   - `config/settings.yaml`
   - `scripts/groq_whisper_proxy.py`
2. 页面数据一致性风险：
   - `api/routes/pages.py` 的 `recent_completed_partial` 对时间字段按 ISO 解析，但任务时间戳是 float。
   - `api/routes/pages.py` 使用 `target_platforms` 字段做过滤/展示，任务模型默认未持久化该字段。
3. 运维可移植性风险：
   - `scripts/start_all.sh` 中 KlicStudio 路径和 ffmpeg-full 路径写死为本机路径。
