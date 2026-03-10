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
- 兼容性说明：
  - 历史任务 JSON 中的 `klic_task_id` / `klic_progress` 会在加载时自动迁移到新字段
  - 旧 `settings.yaml` 中若仍存在 `klicstudio` provider，读取时会自动归一化为当前支持值

## 自检
- 代码风格与注释
- 异常路径
- 关键日志
- 页面主链路回归（列表 / 新建 / 详情）
