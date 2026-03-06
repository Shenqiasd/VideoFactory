# Step 3: 实施 - 火山引擎翻译+TTS 完整集成

**开始时间**: 2026-03-04 16:20
**负责人**: Codex
**预计工期**: 5天

---

## 📋 实施任务清单

### Day 1: 火山方舟翻译 (后端)

**任务 1.1**: 创建翻译模块基础架构
- 文件: `src/translation/__init__.py`
- 文件: `src/translation/base.py`
- 定义 `BaseTranslator` 接口

**任务 1.2**: 实现火山方舟翻译
- 文件: `src/translation/volcengine_ark.py`
- 使用 OpenAI SDK
- 配置: base_url + api_key + model

**任务 1.3**: 重构现有翻译逻辑
- 文件: `src/translation/llm_translator.py`
- 将现有翻译逻辑迁移到新模块

**任务 1.4**: 集成到主流程
- 文件: `src/production/pipeline.py`
- 替换现有翻译调用为翻译路由

---

### Day 2: 火山方舟翻译 (前端 + 测试)

**任务 2.1**: 前端配置界面
- 文件: `web/templates/settings.html`
- 新增"火山方舟翻译"配置区
- 字段: 启用开关、Base URL、API Key、Model

**任务 2.2**: 测试接口
- 文件: `web/app.py`
- 实现 `POST /api/test/translation`
- 输入: text, provider, target_lang
- 输出: success, result, error

**任务 2.3**: 前端测试功能
- 文件: `web/templates/settings.html`
- 测试按钮 + 结果显示区域
- JavaScript 调用测试接口

---

### Day 3: 火山引擎 TTS (后端)

**任务 3.1**: 修改 VolcengineTTS
- 文件: `src/tts/volcengine_tts.py`
- 改为 HTTP API 调用
- URL: `https://openspeech.bytedance.com/api/v1/tts`
- 认证: appid + access_token

**任务 3.2**: 新增音色参数
- 修改 `synthesize()` 方法
- 新增 `voice_type` 参数
- 支持音色选择

**任务 3.3**: 配置音色列表
- 文件: `config/settings.yaml`
- 新增 `tts.volcengine.available_voices`
- 预置 6 种常用音色

---

### Day 4: 火山引擎 TTS (前端 + 测试)

**任务 4.1**: 前端音色选择
- 文件: `web/templates/settings.html`
- 音色下拉选择框
- 从 `/api/tts/voices` 动态加载

**任务 4.2**: TTS 测试接口
- 文件: `web/app.py`
- 实现 `POST /api/test/tts`
- 实现 `GET /api/tts/voices`
- 实现 `GET /api/audio/{filename}`

**任务 4.3**: 前端测试功能
- 测试按钮 + 音频播放器
- JavaScript 调用测试接口
- 播放返回的音频

---

### Day 5: 集成测试

**任务 5.1**: 端到端测试
- 测试完整翻译+配音流程
- 验证火山方舟翻译
- 验证火山引擎 TTS

**任务 5.2**: 降级策略测试
- 测试翻译降级（火山 → LLM）
- 测试 TTS 降级（火山 → KlicStudio）

**任务 5.3**: 前端测试功能验证
- 测试翻译测试按钮
- 测试 TTS 测试按钮
- 测试音色选择

---

## 📝 实施注意事项

1. **OpenAI SDK 兼容性**: 火山方舟使用 OpenAI 兼容 API，直接使用 `openai` 库
2. **音色 ID 格式**: 火山引擎音色 ID 格式为 `BV001_streaming`
3. **HTTP API 响应**: TTS 响应可能是 JSON（包含 base64）或直接二进制
4. **测试接口安全**: 测试接口应限制调用频率，避免滥用
5. **配置向后兼容**: 默认配置保持现有行为

---

## 🔗 参考文档

- 需求文档: `workflow/steps/requirements_volcengine_integration.md`
- 设计文档: `workflow/steps/step2_design_volcengine_integration.md`
- 火山方舟控制台: https://console.volcengine.com/ark/
- 火山引擎 TTS 文档: https://www.volcengine.com/docs/6561/1719100

---

## ✅ 完成标准

- [ ] 火山方舟翻译后端实现
- [ ] 火山引擎 TTS HTTP API 实现
- [ ] 音色选择功能
- [ ] 前端配置界面完整
- [ ] 测试接口可用（翻译 + TTS）
- [ ] 端到端测试通过
- [ ] 降级策略验证通过

