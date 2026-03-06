# Step 1: 需求 - 翻译配音质量升级

**创建时间**: 2026-03-04 14:58
**优先级**: P0 (最高优先级)
**状态**: 需求分析中

---

## 🎯 问题背景

当前翻译配音质量差，核心问题：
1. **ASR 质量差**: 当前依赖 KlicStudio 的 Whisper，识别准确率不足
2. **翻译质量差**: 翻译模型效果不理想
3. **配音质量差**: TTS 音色和自然度不够
4. **流程不灵活**: 无法切换不同服务商（阿里云、火山引擎）

---

## 📋 核心需求

### 1. ASR (语音识别) 升级

**当前问题**:
- KlicStudio 使用 OpenAI Whisper API
- 本地 Whisper 代理已实现但未集成

**需求**:
- ✅ 支持本地 Whisper (已有 `scripts/whisper_proxy.py`)
- 🆕 支持阿里云 ASR (Fun-ASR / Paraformer / Gummy)
- 🆕 支持火山引擎 ASR (豆包 SeedASR 2.0)
- 🆕 支持直接获取 YouTube 字幕 (无需 ASR)

### 2. 翻译服务升级

**当前问题**:
- 翻译质量不稳定

**需求**:
- 保留现有 LLM 翻译能力
- 支持多模型切换 (GPT-4 / Claude / 国产大模型)

### 3. TTS (语音合成) 升级

**当前问题**:
- 配音音色单一，自然度不够

**需求**:
- 🆕 支持阿里云 CosyVoice v3.5 (语音克隆)
- 🆕 支持火山引擎语音复刻 V3
- 保留现有 TTS 能力作为备选

---

## 🔧 技术方案

### 方案 1: YouTube 字幕直接获取 (最快)

**适用场景**: 仅翻译任务，无需配音

**流程**:
```
YouTube URL
  ↓
youtube-transcript-api 获取字幕
  ↓
LLM 翻译
  ↓
输出翻译字幕
```

**优势**:
- 无需 ASR，速度快
- 成本低

**限制**:
- 仅适用于有字幕的 YouTube 视频
- 无法配音

### 方案 2: 多服务商 ASR + TTS

**流程**:
```
视频文件
  ↓
音频分离 (FFmpeg)
  ↓
ASR (阿里云/火山/本地Whisper)
  ↓
LLM 翻译
  ↓
TTS (阿里云CosyVoice/火山语音复刻)
  ↓
音频混合 + 视频合成
```

---

## 📊 服务商对比

### 阿里云 (Model Studio)

**ASR**:
- fun-asr-realtime (稳定版)
- paraformer-realtime-v2
- gummy-realtime-v1
- 接入: WebSocket
- 文档: https://help.aliyun.com/zh/model-studio/real-time-speech-recognition

**TTS (CosyVoice)**:
- cosyvoice-v3.5-plus / v3.5-flash
- 支持语音克隆
- 流程: 创建音色 → 合成
- 文档: https://help.aliyun.com/zh/model-studio/cosyvoice-clone-api

### 火山引擎 (豆包语音)

**ASR**:
- SeedASR 2.0 (推荐)
- WebSocket 接入
- 文档: https://www.volcengine.com/docs/6561/1354869

**TTS**:
- 语音复刻 V3
- 支持音色训练

---

## 🎯 实施优先级

### Phase 1: YouTube 字幕直接获取 (1-2天)
- [ ] 集成 youtube-transcript-api
- [ ] 支持字幕下载 + 翻译
- [ ] 跳过 ASR 流程

### Phase 2: 本地 Whisper 集成 (1天)
- [ ] 将 `scripts/whisper_proxy.py` 集成到主流程
- [ ] 支持音频分离

### Phase 3: 阿里云服务集成 (3-5天)
- [ ] ASR: Fun-ASR WebSocket
- [ ] TTS: CosyVoice v3.5

### Phase 4: 火山引擎集成 (3-5天)
- [ ] ASR: SeedASR 2.0
- [ ] TTS: 语音复刻 V3

---

## ✅ 完成定义

- [ ] 支持 YouTube 字幕直接获取
- [ ] 支持本地 Whisper
- [ ] 支持阿里云 ASR + TTS
- [ ] 支持火山引擎 ASR + TTS
- [ ] 配置化切换服务商
- [ ] 翻译配音质量显著提升

