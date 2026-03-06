# Step 2: 技术设计 - 翻译配音质量升级

**创建时间**: 2026-03-04 15:05
**设计者**: Claude
**状态**: 设计中

---

## 🎯 设计范围

实施 Phase 1 + 2 + 4:
1. **Phase 1**: YouTube 字幕直接获取
2. **Phase 2**: 本地 Whisper 集成
3. **Phase 4**: 火山引擎 ASR + TTS

---

## 🏗️ 整体架构

### 新架构设计

```
视频输入
  ↓
┌─────────────────────────────────────┐
│ ASR 路由层 (新增)                    │
│ - YouTube字幕获取                    │
│ - 本地Whisper                        │
│ - 火山引擎SeedASR                    │
└─────────────────────────────────────┘
  ↓
字幕SRT
  ↓
┌─────────────────────────────────────┐
│ 翻译层 (已有)                        │
│ - LLM翻译                            │
└─────────────────────────────────────┘
  ↓
翻译后字幕
  ↓
┌─────────────────────────────────────┐
│ TTS 路由层 (新增)                    │
│ - 火山引擎语音复刻V3                 │
│ - KlicStudio TTS (备选)             │
└─────────────────────────────────────┘
  ↓
配音音频
  ↓
视频合成
```

---

## 📦 新增模块结构

```
src/asr/
├── __init__.py
├── base.py                    # ASR基类
├── youtube_subtitle.py        # YouTube字幕获取
├── whisper_local.py           # 本地Whisper
└── volcengine_asr.py          # 火山引擎ASR

src/tts/
├── __init__.py
├── base.py                    # TTS基类
└── volcengine_tts.py          # 火山引擎TTS
```


---

## 🔍 Phase 1: YouTube 字幕获取

### 模块: YouTubeSubtitleFetcher

**职责**: 直接从 YouTube 获取字幕，跳过 ASR

**核心接口**:
```python
class YouTubeSubtitleFetcher:
    async def fetch(self, video_url: str, lang: str = "en") -> Optional[str]:
        """
        返回: SRT 格式字幕文本
        """
```

**实现方案**:
```python
# 使用 youtube-transcript-api
from youtube_transcript_api import YouTubeTranscriptApi

# 1. 提取 video_id
# 2. 获取字幕列表
# 3. 优先获取手动字幕，降级到自动字幕
# 4. 转换为 SRT 格式
```

**依赖**:
```
youtube-transcript-api>=0.6.0
```

**优势**:
- 无需下载视频
- 无需 ASR 处理
- 速度快（<5秒）
- 成本低

**限制**:
- 仅适用于有字幕的视频
- 无法配音（需配合 TTS）


---

## 🎤 Phase 2: 本地 Whisper 集成

### 模块: WhisperLocalASR

**职责**: 使用本地 Whisper 模型进行 ASR

**核心接口**:
```python
class WhisperLocalASR:
    async def transcribe(
        self, 
        audio_path: str, 
        language: str = "en",
        model: str = "base"
    ) -> str:
        """返回: SRT 格式字幕"""
```

**实现方案**:
```python
# 复用已有 scripts/whisper_proxy.py
# 1. 音频分离 (FFmpeg)
# 2. 调用本地 Whisper
# 3. 返回 SRT
```

**音频分离**:
```bash
ffmpeg -i video.mp4 -vn -acodec pcm_s16le -ar 16000 audio.wav
```

**依赖**:
```
openai-whisper>=20231117
ffmpeg-python>=0.2.0
```

**性能**:
- base 模型: ~1x 实时速度
- 60分钟视频 ≈ 60分钟处理


---

## 🔥 Phase 4: 火山引擎集成

### 模块 1: VolcengineASR (SeedASR 2.0)

**职责**: 使用火山引擎 SeedASR 2.0 进行实时语音识别

**核心接口**:
```python
class VolcengineASR:
    async def transcribe(self, audio_path: str, language: str = "zh-CN") -> str:
        """返回: SRT 格式字幕"""
```

**实现方案**:
```python
# WebSocket 流式识别
# 1. 建立 WebSocket 连接
# 2. 分块发送音频数据
# 3. 接收实时识别结果
# 4. 组装为 SRT
```

**WebSocket 接口**:
```
wss://openspeech.bytedance.com/api/v3/sauc/bigmodel
```

**认证**:
```python
# 使用 appid + token
headers = {
    "Authorization": f"Bearer {token}"
}
```

**资源代号**:
```
volc.seedasr.sauc.duration  # 按时长计费
```


### 模块 2: VolcengineTTS (语音复刻 V3)

**职责**: 使用火山引擎语音复刻 V3 进行配音

**核心接口**:
```python
class VolcengineTTS:
    async def clone_voice(self, audio_sample: str) -> str:
        """返回: voice_id"""
    
    async def synthesize(self, text: str, voice_id: str) -> str:
        """返回: 音频文件路径"""
```

**实现流程**:
```
1. 提取原视频音频片段 (3-10秒)
2. 调用音色训练接口 → voice_id
3. 使用 voice_id 合成翻译文本
4. 返回配音音频
```

**API 端点**:
```
POST /api/v3/voice/clone  # 音色训练
POST /api/v3/tts/synthesis  # 语音合成
```


---

## 🔀 ASR 路由层设计

### 模块: ASRRouter

**职责**: 根据配置选择合适的 ASR 服务

**核心接口**:
```python
class ASRRouter:
    async def transcribe(
        self, 
        video_url: str,
        video_path: Optional[str] = None,
        source_lang: str = "en"
    ) -> tuple[str, str]:
        """
        返回: (srt_content, method_used)
        method_used: youtube/whisper/volcengine
        """
```

**路由逻辑**:
```python
# 1. 如果是 YouTube URL 且配置启用 → YouTubeSubtitleFetcher
# 2. 如果配置为 volcengine → VolcengineASR
# 3. 否则 → WhisperLocalASR (默认)
```

**配置示例**:
```yaml
asr:
  provider: "auto"  # auto/youtube/whisper/volcengine
  youtube:
    enabled: true
    fallback_to_whisper: true
  whisper:
    model: "base"
  volcengine:
    appid: "${VOLC_APPID}"
    token: "${VOLC_TOKEN}"
```


---

## 📊 数据流设计

### 完整流程

```
YouTube URL
  ↓
ASRRouter.transcribe()
  ├─ YouTube字幕? → YouTubeSubtitleFetcher
  ├─ 火山引擎? → VolcengineASR
  └─ 默认 → WhisperLocalASR
  ↓
原文字幕 SRT
  ↓
LLM 翻译 (已有)
  ↓
翻译字幕 SRT
  ↓
TTS (可选)
  ├─ 火山引擎 → VolcengineTTS
  └─ KlicStudio (备选)
  ↓
配音音频
  ↓
视频合成
```

### 关键数据结构

```python
# ASR 结果
{
    "srt_content": "1\n00:00:00,000 --> 00:00:05,000\nHello world\n",
    "method": "youtube",  # youtube/whisper/volcengine
    "language": "en",
    "duration": 125.5
}

# TTS 结果
{
    "audio_path": "/path/to/dubbed.wav",
    "voice_id": "voice_xxx",
    "method": "volcengine"
}
```


---

## 📋 模块影响清单

### 新增文件 (7个)

```
src/asr/
├── __init__.py              # ~20 行
├── base.py                  # ~30 行
├── youtube_subtitle.py      # ~80 行
├── whisper_local.py         # ~100 行
└── volcengine_asr.py        # ~200 行

src/tts/
├── __init__.py              # ~20 行
├── base.py                  # ~30 行
└── volcengine_tts.py        # ~150 行
```

### 修改文件 (2个)

```
src/production/pipeline.py
- 替换 KlicStudio ASR 为 ASRRouter
- 新增 TTS 路由逻辑

config/settings.yaml
- 新增 asr 配置段
- 新增 tts 配置段
```

### 依赖变更

```
requirements.txt 新增:
youtube-transcript-api>=0.6.0
websockets>=12.0
```


---

## ⚙️ 配置设计

### config/settings.yaml

```yaml
asr:
  provider: "auto"  # auto/youtube/whisper/volcengine
  
  youtube:
    enabled: true
    fallback_to_whisper: true
  
  whisper:
    model: "base"  # tiny/base/small/medium
    device: "cpu"
  
  volcengine:
    enabled: false
    appid: "${VOLC_APPID}"
    token: "${VOLC_TOKEN}"
    resource: "volc.seedasr.sauc.duration"

tts:
  provider: "klicstudio"  # klicstudio/volcengine
  
  volcengine:
    enabled: false
    appid: "${VOLC_APPID}"
    token: "${VOLC_TOKEN}"
    voice_sample_duration: 5  # 秒
```


---

## 🚨 错误处理

### 降级策略

```python
# 1. YouTube 字幕获取失败 → 降级到 Whisper
if youtube_fetch_failed:
    fallback_to_whisper()

# 2. 火山引擎 ASR 失败 → 降级到 Whisper
if volcengine_asr_failed:
    fallback_to_whisper()

# 3. 火山引擎 TTS 失败 → 降级到 KlicStudio
if volcengine_tts_failed:
    fallback_to_klicstudio()
```

---

## 📅 实施计划

### Day 1-2: Phase 1 (YouTube 字幕)
- [ ] 实现 YouTubeSubtitleFetcher
- [ ] 集成到 ASRRouter
- [ ] 测试 YouTube 字幕获取

### Day 3: Phase 2 (本地 Whisper)
- [ ] 实现 WhisperLocalASR
- [ ] 音频分离逻辑
- [ ] 集成到 ASRRouter

### Day 4-6: Phase 4 ASR (火山引擎)
- [ ] 实现 VolcengineASR
- [ ] WebSocket 连接
- [ ] 流式识别

### Day 7-8: Phase 4 TTS (火山引擎)
- [ ] 实现 VolcengineTTS
- [ ] 音色训练
- [ ] 语音合成

### Day 9: 集成测试
- [ ] 端到端测试
- [ ] 降级策略测试

---

## ✅ 完成定义

- [x] ASR 路由层设计完成
- [x] 3种 ASR 方案设计完成
- [x] TTS 方案设计完成
- [x] 配置方案设计完成
- [x] 降级策略设计完成
- [x] 实施计划明确

**下一步**: 进入 Step 3 实施

