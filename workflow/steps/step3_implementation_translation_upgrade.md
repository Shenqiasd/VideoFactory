# Step 3: 实施 - 翻译配音质量升级

**开始时间**: 2026-03-04 15:15
**负责人**: Codex
**预计工期**: 9天

---

## 📋 实施任务清单

### Phase 1: YouTube 字幕获取 (Day 1-2)

**任务 1.1**: 创建 YouTubeSubtitleFetcher
- 文件: `src/asr/youtube_subtitle.py`
- 依赖: `youtube-transcript-api>=0.6.0`
- 核心功能:
  - 提取 video_id
  - 获取字幕（优先手动，降级自动）
  - 转换为 SRT 格式

**任务 1.2**: 创建 ASR 基类
- 文件: `src/asr/base.py`
- 定义统一接口

**任务 1.3**: 测试
- 测试 YouTube 字幕获取
- 验证 SRT 格式输出

---

### Phase 2: 本地 Whisper 集成 (Day 3)

**任务 2.1**: 创建 WhisperLocalASR
- 文件: `src/asr/whisper_local.py`
- 复用: `scripts/whisper_proxy.py` 的逻辑
- 核心功能:
  - FFmpeg 音频分离
  - 调用 Whisper 模型
  - 返回 SRT

**任务 2.2**: 测试
- 测试音频分离
- 测试 Whisper 识别

---

### Phase 4: 火山引擎集成 (Day 4-8)

**任务 4.1**: 创建 VolcengineASR (Day 4-6)
- 文件: `src/asr/volcengine_asr.py`
- 依赖: `websockets>=12.0`
- 核心功能:
  - WebSocket 连接
  - 流式音频发送
  - 实时识别结果接收
  - 组装 SRT

**任务 4.2**: 创建 VolcengineTTS (Day 7-8)
- 文件: `src/tts/volcengine_tts.py`
- 核心功能:
  - 音色训练（提取音频样本）
  - 语音合成

**任务 4.3**: 测试
- 测试火山引擎 ASR
- 测试火山引擎 TTS

---

### 集成任务 (Day 9)

**任务 5.1**: 创建 ASRRouter
- 文件: `src/asr/__init__.py`
- 路由逻辑: YouTube → 火山 → Whisper

**任务 5.2**: 修改 ProductionPipeline
- 文件: `src/production/pipeline.py`
- 替换 KlicStudio ASR 为 ASRRouter
- 集成 TTS 路由

**任务 5.3**: 配置文件
- 文件: `config/settings.yaml`
- 添加 asr 和 tts 配置段

**任务 5.4**: 端到端测试
- 测试完整流程
- 测试降级策略

---

## 📝 实施注意事项

1. **最小化实现**: 每个模块只实现核心功能，避免过度设计
2. **测试优先**: 每完成一个模块立即测试
3. **降级策略**: 确保每个服务失败时有降级方案
4. **配置化**: 所有服务商切换通过配置完成
5. **向后兼容**: 默认配置不影响现有功能

---

## 🔗 参考文档

- 需求文档: `workflow/steps/step1_requirements_translation_upgrade.md`
- 设计文档: `workflow/steps/step2_design_translation_upgrade.md`
- 现有代码: `scripts/whisper_proxy.py`, `src/production/pipeline.py`

---

## ✅ 完成标准

- [x] 3种 ASR 方案全部实现
- [x] 火山引擎 TTS 实现
- [x] ASRRouter 路由正常工作
- [x] 配置文件完整
- [x] 端到端测试通过（代码级：单测+集成测试+全量pytest）
- [x] 降级策略验证通过

---

## 📦 本次实现结果（Codex）

### 新增文件
- `src/asr/base.py`
- `src/asr/youtube_subtitle.py`
- `src/asr/whisper_local.py`
- `src/asr/volcengine_asr.py`
- `src/asr/__init__.py`
- `src/tts/base.py`
- `src/tts/volcengine_tts.py`
- `src/tts/__init__.py`
- `tests/test_asr_youtube_subtitle.py`
- `tests/test_asr_router.py`
- `tests/test_production_asr_router.py`

### 修改文件
- `src/production/pipeline.py`
- `src/production/subtitle_repair.py`
- `config/settings.yaml`
- `requirements.txt`

### 关键行为
1. ASR 路由支持：`YouTube -> Volcengine -> Whisper`，翻译主流程已不再依赖 `KlicStudio`。
2. `ProductionPipeline` 现在直接在主链路中完成：
   - `origin_language_srt.srt`
   - `target_language_srt.srt`
   - `bilingual_srt.srt`
   - `origin_language.txt`
   - `target_language.txt`
   - `translated_title`
   - `translated_description`
3. 启用 TTS 时，主链路直接调用 `VolcengineTTS` 并在本地重建 `output/video_with_tts.mp4`。
4. `yt-dlp` 的 YouTube JS runtime 失败不再隐式回退 KlicStudio，而是明确标记任务失败，便于排障。
5. 旧 `klicstudio` provider/config 字段仅保留兼容用途，不再作为推荐主路径。

### 测试结果
- 新增/受影响测试：
  - `python3.11 -m pytest -q tests/test_production_asr_router.py tests/test_production_submit_failures.py tests/test_production_download_retry.py tests/web/test_download_fallback.py tests/web/test_api_contract.py -k 'asr_tts_settings or download_fallback or production_asr_router or production_submit_failures or step_download'`
  - 结果：`13 passed`
- 回归验证：
  - `python3.11 -m pytest -q tests/web/test_api_contract.py tests/test_production_asr_router.py tests/test_production_submit_failures.py tests/web/test_download_fallback.py`
  - 结果：`46 passed`
