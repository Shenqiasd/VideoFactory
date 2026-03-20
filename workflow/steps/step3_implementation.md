# Step 3 - 实现开发

## 目标
完成 KlicStudio 旧链路依赖清理，保持现有自管翻译配音主路径可用。

## 改动记录
- 文件：
  - `src/production/pipeline.py`
  - `src/asr/__init__.py`
  - `api/routes/system.py`
  - `api/routes/pages.py`
  - `web/templates/tasks.html`
  - `web/templates/task_detail.html`
  - `web/templates/new_task.html`
  - `web/templates/partials/task_list.html`
  - `web/templates/settings.html`
  - `scripts/start_all.sh`
  - `config/settings.example.yaml`
  - `README.md`
  - `tests/test_production_asr_router.py`
  - `tests/web/test_api_contract.py`
  - `tests/web/test_partials_http.py`
  - `tests/e2e/test_frontend_playwright.py`
- 关键改动点：
  - 删除 `KlicStudioClient` 与生产管线中的旧提交/轮询/下载逻辑
  - 系统设置页/API 不再允许保存 `klicstudio` 作为 ASR/TTS provider
  - 启动脚本与示例配置不再启动/配置 KlicStudio 服务
  - 仪表盘服务状态不再探测 8888
  - 任务/生产状态 API 改用 `translation_task_id` / `translation_progress`
  - 页面层统一以持久化 `task.progress` 为主、状态映射为辅，避免 `/tasks`、dashboard 与主 API 进度不一致
  - `TaskState.active_states()` 补入 `qc_passed`，确保 UI、任务统计接口与系统状态统计一致
  - 任务列表支持保持 `?status=` 查询参数，筛选状态下删除任务会按当前过滤条件刷新
  - 任务详情页补齐翻译/QC 元信息，并能基于 `timeline` 展示失败任务卡在哪个阶段
  - Hotfix：补齐 `yt-dlp` Python 依赖，并让下载链路优先解析当前 `.venv` 内的 `yt-dlp`，避免运行中的 API/Worker 因 PATH 缺失直接抛 `[Errno 2]`
  - Hotfix：`uploading_source` 阶段改为 best-effort，缺失 `rclone` 或 R2 不可用时继续走本地源视频主链路，不再直接 fail
  - Hotfix：`YouTubeSubtitleASR` 在 Transcript API 为空时自动回退 `yt-dlp` 抓取自动字幕，降低对 Whisper/Groq key 的依赖
  - Hotfix：`yt-dlp` 自动字幕优先解析 `srv3` 原始格式，并对 `vtt/srt` 滚动字幕做去重规范化，避免 `fans have asked ...` 这类重复片段污染逐行翻译
  - 新增：`SentenceRegrouper` 在主翻译阶段先将连续碎片 cue 合并成 sentence group，再翻译并投影回原 cue 数量，替代“纯逐行翻译”造成的语义断裂
  - Hotfix：对火山逐行翻译后仍保留纯英文的碎片句追加“前后文补翻”，优先修复 `released and every year thousands of old` 这类漏翻场景
  - Hotfix：`QualityChecker` 新增英文残留检测，避免存在明显英文漏翻时仍返回 `qc_score=100`
  - 新增：创建任务时自动解析原视频标题并生成 `project_name`（`translated_title`），停止使用字幕第一句作为标题兜底
  - 新增：任务列表 / Dashboard / 任务详情 / 发布队列统一按 `project_name` 展示
  - 新增：下载产物统一命名为 `项目名_产物类型[_平台|序号].ext`，并提供历史任务标题回填脚本
- 兼容性说明：
  - 历史任务 JSON 中的 `klic_task_id` / `klic_progress` 会在加载时自动迁移到新字段
  - 旧 `settings.yaml` 中若仍存在 `klicstudio` provider，读取时会自动归一化为当前支持值

## 自检
- 代码风格与注释
- 异常路径
- 关键日志
- 页面主链路回归（列表 / 新建 / 详情）

## 2026-03-13 - 字幕翻译回投断句修复

### 目标
修复翻译后字幕按原 cue 数量回投时出现的短语/标题/成对标点被切坏问题，保持主翻译链路、全局复审链路与双语字幕输出兼容。

### 设计依据
- 参考 `subalign`：引入 penalty-based dynamic programming 思路，替代“最近切点”启发式
- 参考 Netflix / Subtitle Edit：增加 no-break 边界规则、短残片回收
- 参考 VideoLingo：保留“先完整翻译、再做字幕切分”的流程分层，但当前阶段不直接引入强 LLM 依赖

### 计划改动
- `src/production/sentence_regrouper.py`
  - 新增 protected span / boundary scoring / penalty-based projection / post-pass rebalance
- `tests/test_sentence_regrouper.py`
  - 补书名号、成对标点、function word 残片等回归用例

### 验证计划
- `./.venv/bin/python -m pytest -q tests/test_sentence_regrouper.py`
- 视影响补跑：`./.venv/bin/python -m pytest -q tests/test_global_translation_reviewer.py`

## 2026-03-13 - `/tasks/new` 前端 E2E 失败修复

### 根因分析
- 原 `/tasks/new` 页面把“创建任务成功后跳转 `/tasks`”实现为 `htmx after-request + setTimeout(location.href='/tasks', 800)`
- 这条链路依赖前端增强脚本按时完成初始化；在 E2E 中页面只等待 `domcontentloaded`，随后立即点击提交，导致“关键跳转依赖 JS/HTMX 时序”过于脆弱
- 页面还存在额外 JS 错误：
  - `web/templates/base.html` 误写 `tailwindcss.config`
  - `web/templates/new_task.html` 里 `previewDebug.*` 对空值直接取属性
- 这些前端错误虽然不是唯一根因，但放大了页面初始化不稳定性

### 实现修复
- `web/templates/new_task.html`
  - 单任务 / 批量任务表单改为原生 `method="post" action="/api/tasks/..."`
  - 移除“成功后靠前端定时器跳转”的关键依赖，让无 HTMX 场景也能正常工作
  - 修复 `previewDebug` 空值访问
- `api/routes/tasks.py`
  - HTMX 请求返回 `HX-Redirect: /tasks`
  - 浏览器原生表单 POST（`Accept: text/html` 或导航请求）返回 `303 /tasks`
  - 保持 API 客户端调用仍返回 JSON，不破坏现有接口契约
- `web/templates/base.html`
  - 修复 Tailwind CDN 配置变量名为 `tailwind.config`

### 验证结果
- `./.venv/bin/python -m pytest -q tests/web/test_api_contract.py tests/e2e/test_frontend_playwright.py` → `62 passed`
- `./.venv/bin/python -m pytest -q` → `188 passed`


## 2026-03-13 - `/tasks` 页面去脆弱化

### 根因分析
- `/tasks` 首屏依赖 Alpine 初始化后调用 `htmx.ajax(...)` 才会真正加载任务列表；缺少前端增强脚本时页面只剩骨架屏
- 状态筛选按钮只有 JS 点击行为，没有 `href` 回退路径
- `base.html` 中的 Lucide 初始化对外部脚本是硬依赖

### 实现修复
- `api/routes/pages.py`
  - 抽出 `_build_task_list_context(...)`，统一 `/tasks` 和 `/web/partials/task_list` 的服务端数据准备
- `web/templates/tasks.html`
  - 首屏直接 include `partials/task_list.html`
  - 筛选项改为可点击链接，HTMX 存在时再增强为局部刷新
  - `taskListFilters()` 对 `window.htmx` 做守卫
- `web/templates/base.html`
  - Lucide 初始化改为安全包装函数
- `web/templates/new_task.html`
  - 补回创作配置面板，保持页面契约完整

### 验证结果
- `./.venv/bin/python -m pytest -q tests/web/test_pages_http.py tests/web/test_partials_http.py tests/e2e/test_frontend_playwright.py` → `37 passed`
- `./.venv/bin/python -m pytest -q` → `191 passed`


## 2026-03-14 - Dashboard 首页去脆弱化

### 根因分析
- 首页多个核心区块依赖 HTMX 首屏异步拉取；缺少前端增强时页面只剩骨架屏
- 这种实现不符合“关键信息无 JS 先可用”的稳定性目标

### 实现修复
- `api/routes/pages.py`
  - 抽出 dashboard 所需的服务端上下文构建函数，统一首页与 partial 数据逻辑
- `web/templates/dashboard.html`
  - 首屏直接 include `stats_cards`、`active_tasks`、`service_status_detail`、`storage_overview`、`recent_completed`
  - 保留 HTMX 属性，仅作为后续自动刷新增强
- `tests/web/test_pages_http.py`
  - 补 dashboard 首屏服务端渲染回归测试

### 验证结果
- `./.venv/bin/python -m pytest -q tests/web/test_pages_http.py tests/web/test_partials_http.py tests/e2e/test_frontend_playwright.py` → `41 passed`
- `./.venv/bin/python -m pytest -q` → `201 passed`
