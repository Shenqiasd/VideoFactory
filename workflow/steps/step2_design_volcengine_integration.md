# Step 2: 技术设计 - 火山引擎翻译+TTS 完整集成

**创建时间**: 2026-03-04 16:15
**设计者**: Claude
**状态**: 设计中

---

## 🎯 设计目标

1. **火山方舟翻译**：使用 `doubao-seed-translation` 替换现有 LLM 翻译
2. **火山引擎 TTS**：HTTP API + 多音色选择
3. **前端配置界面**：完整配置 + 快速测试功能

---

## 🏗️ 整体架构

```
视频输入
  ↓
ASR (已有: YouTube/Whisper/火山ASR)
  ↓
原文字幕 SRT
  ↓
┌─────────────────────────────────────┐
│ 翻译路由层 (新增)                    │
│ - 火山方舟翻译 (OpenAI兼容)          │
│ - 现有 LLM 翻译 (备选)               │
└─────────────────────────────────────┘
  ↓
翻译字幕 SRT
  ↓
┌─────────────────────────────────────┐
│ TTS 路由层 (改进)                    │
│ - 火山引擎 TTS (HTTP API)            │
│   - 多音色选择                       │
│ - KlicStudio TTS (备选)             │
└─────────────────────────────────────┘
  ↓
配音音频
  ↓
视频合成
```

---

## 📦 新增/修改模块

```
src/translation/
├── __init__.py              # 新增
├── base.py                  # 新增：翻译基类
├── volcengine_ark.py        # 新增：火山方舟翻译
└── llm_translator.py        # 重构：现有 LLM 翻译

src/tts/
└── volcengine_tts.py        # 修改：适配 HTTP API + 音色

web/templates/
└── settings.html            # 修改：新增配置区 + 测试按钮

web/
└── app.py                   # 新增：测试接口
```

---

## 🔍 模块 1: 火山方舟翻译

### 核心接口

```python
class VolcengineArkTranslator(BaseTranslator):
    """火山方舟翻译（OpenAI 兼容）"""

    def __init__(self, config: Config):
        self.base_url = config.get("translation.volcengine_ark.base_url")
        self.api_key = config.get("translation.volcengine_ark.api_key")
        self.model = config.get("translation.volcengine_ark.model")
        self.client = OpenAI(base_url=self.base_url, api_key=self.api_key)

    async def translate(
        self,
        text: str,
        source_lang: str = "en",
        target_lang: str = "zh"
    ) -> str:
        """翻译文本"""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": f"Translate from {source_lang} to {target_lang}"},
                {"role": "user", "content": text}
            ]
        )
        return response.choices[0].message.content
```

### 配置项

```yaml
translation:
  provider: "volcengine_ark"  # volcengine_ark / llm

  volcengine_ark:
    enabled: true
    base_url: "https://ark.cn-beijing.volces.com/api/v3"
    api_key: ""
    model: "doubao-seed-translation"  # 或 endpoint ID
    timeout: 60
```

---

## 🎤 模块 2: 火山引擎 TTS (改进)

### 核心接口

```python
class VolcengineTTS(BaseTTSProvider):
    """火山引擎 TTS (HTTP API)"""

    async def synthesize(
        self,
        text: str,
        output_path: str,
        voice_type: str = "BV001_streaming",
        **kwargs
    ) -> Optional[TTSResult]:
        """
        语音合成

        Args:
            text: 合成文本
            output_path: 输出路径
            voice_type: 音色 ID
        """
        url = "https://openspeech.bytedance.com/api/v1/tts"

        payload = {
            "app": {
                "appid": self.appid,
                "token": self.access_token,
                "cluster": self.cluster
            },
            "user": {"uid": "user_001"},
            "audio": {
                "voice_type": voice_type,
                "encoding": "mp3",
                "speed_ratio": 1.0,
                "volume_ratio": 1.0,
                "pitch_ratio": 1.0
            },
            "request": {
                "reqid": str(uuid.uuid4()),
                "text": text,
                "text_type": "plain",
                "operation": "query"
            }
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload)

        # 处理响应（可能是 JSON 或二进制）
        if resp.headers.get("content-type") == "audio/mpeg":
            Path(output_path).write_bytes(resp.content)
        else:
            data = resp.json()
            audio_data = base64.b64decode(data["data"])
            Path(output_path).write_bytes(audio_data)

        return TTSResult(audio_path=output_path, provider="volcengine")
```

### 音色配置

```yaml
tts:
  provider: "volcengine"

  volcengine:
    enabled: true
    appid: ""
    access_token: ""
    cluster: "volcano_tts"
    default_voice: "BV001_streaming"

    # 可选音色列表
    available_voices:
      - id: "BV001_streaming"
        name: "通用女声"
        language: "zh-CN"
      - id: "BV002_streaming"
        name: "通用男声"
        language: "zh-CN"
      - id: "BV700_streaming"
        name: "知性女声"
        language: "zh-CN"
      - id: "BV701_streaming"
        name: "温柔女声"
        language: "zh-CN"
      - id: "BV702_streaming"
        name: "活力女声"
        language: "zh-CN"
      - id: "BV705_streaming"
        name: "磁性男声"
        language: "zh-CN"
```

---

## 🖥️ 模块 3: 前端配置界面

### 翻译配置区

```html
<div class="rounded-2xl border border-border p-4 space-y-3">
    <h3 class="text-[13px] font-semibold text-fg-strong">火山方舟翻译</h3>

    <label class="flex items-center justify-between rounded-xl border border-border px-3 py-2">
        <span class="text-[13px] text-fg">启用火山方舟翻译</span>
        <input id="translation-volcengine-enabled" type="checkbox" />
    </label>

    <div class="grid grid-cols-2 gap-4">
        <div>
            <label class="block text-[12px] text-fg-sub mb-1.5">Base URL</label>
            <input id="translation-volcengine-base-url" type="text"
                   placeholder="https://ark.cn-beijing.volces.com/api/v3" />
        </div>
        <div>
            <label class="block text-[12px] text-fg-sub mb-1.5">API Key</label>
            <input id="translation-volcengine-api-key" type="password" />
        </div>
    </div>

    <div>
        <label class="block text-[12px] text-fg-sub mb-1.5">Model / Endpoint ID</label>
        <input id="translation-volcengine-model" type="text"
               placeholder="doubao-seed-translation" />
    </div>

    <button id="test-translation-btn"
            class="w-full h-10 rounded-2xl bg-fg-strong text-white hover:bg-fg">
        测试翻译
    </button>

    <div id="translation-test-result" class="hidden rounded-xl bg-muted p-3">
        <p class="text-[12px] text-fg-sub">测试结果：</p>
        <p id="translation-result-text" class="text-[13px] text-fg-strong mt-1"></p>
    </div>
</div>
```

### TTS 配置区

```html
<div class="rounded-2xl border border-border p-4 space-y-3">
    <h3 class="text-[13px] font-semibold text-fg-strong">火山引擎 TTS</h3>

    <label class="flex items-center justify-between rounded-xl border border-border px-3 py-2">
        <span class="text-[13px] text-fg">启用火山 TTS</span>
        <input id="tts-volcengine-enabled" type="checkbox" />
    </label>

    <div class="grid grid-cols-2 gap-4">
        <div>
            <label class="block text-[12px] text-fg-sub mb-1.5">App ID</label>
            <input id="tts-volcengine-appid" type="text" />
        </div>
        <div>
            <label class="block text-[12px] text-fg-sub mb-1.5">Access Token</label>
            <input id="tts-volcengine-token" type="password" />
        </div>
    </div>

    <div>
        <label class="block text-[12px] text-fg-sub mb-1.5">默认音色</label>
        <select id="tts-volcengine-voice" class="w-full h-10 px-4 rounded-2xl border">
            <option value="BV001_streaming">通用女声</option>
            <option value="BV002_streaming">通用男声</option>
            <option value="BV700_streaming">知性女声</option>
            <option value="BV701_streaming">温柔女声</option>
            <option value="BV702_streaming">活力女声</option>
            <option value="BV705_streaming">磁性男声</option>
        </select>
    </div>

    <div>
        <label class="block text-[12px] text-fg-sub mb-1.5">测试文本</label>
        <input id="tts-test-text" type="text"
               placeholder="你好，这是语音合成测试"
               class="w-full h-10 px-4 rounded-2xl border" />
    </div>

    <button id="test-tts-btn"
            class="w-full h-10 rounded-2xl bg-fg-strong text-white hover:bg-fg">
        测试语音合成
    </button>

    <div id="tts-test-result" class="hidden rounded-xl bg-muted p-3">
        <audio id="tts-audio-player" controls class="w-full"></audio>
    </div>
</div>
```

---

## 🧪 模块 4: 测试接口

### 后端 API

```python
# web/app.py

@app.post("/api/test/translation")
async def test_translation(request: Request):
    """测试翻译"""
    data = await request.json()
    provider = data.get("provider", "volcengine_ark")
    text = data.get("text", "Hello world")

    try:
        if provider == "volcengine_ark":
            translator = VolcengineArkTranslator(config)
            result = await translator.translate(text, "en", "zh")
        else:
            return {"success": False, "error": "Unknown provider"}

        return {
            "success": True,
            "result": result,
            "provider": provider
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/test/tts")
async def test_tts(request: Request):
    """测试 TTS"""
    data = await request.json()
    provider = data.get("provider", "volcengine")
    text = data.get("text", "你好世界")
    voice_type = data.get("voice_type", "BV001_streaming")

    try:
        if provider == "volcengine":
            tts = VolcengineTTS(config)
            output_path = f"/tmp/tts_test_{uuid.uuid4()}.mp3"
            result = await tts.synthesize(text, output_path, voice_type)

            if result:
                # 返回音频文件 URL
                return {
                    "success": True,
                    "audio_url": f"/api/audio/{Path(output_path).name}",
                    "provider": provider
                }

        return {"success": False, "error": "TTS failed"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/audio/{filename}")
async def serve_audio(filename: str):
    """提供音频文件"""
    file_path = f"/tmp/{filename}"
    return FileResponse(file_path, media_type="audio/mpeg")
```

### 前端 JavaScript

```javascript
// 测试翻译
document.getElementById('test-translation-btn').addEventListener('click', async () => {
    const btn = event.target;
    btn.disabled = true;
    btn.textContent = '测试中...';

    try {
        const resp = await fetch('/api/test/translation', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                provider: 'volcengine_ark',
                text: 'Hello world, this is a translation test.'
            })
        });

        const data = await resp.json();

        if (data.success) {
            document.getElementById('translation-result-text').textContent = data.result;
            document.getElementById('translation-test-result').classList.remove('hidden');
        } else {
            alert('测试失败: ' + data.error);
        }
    } finally {
        btn.disabled = false;
        btn.textContent = '测试翻译';
    }
});

// 测试 TTS
document.getElementById('test-tts-btn').addEventListener('click', async () => {
    const btn = event.target;
    const text = document.getElementById('tts-test-text').value;
    const voice = document.getElementById('tts-volcengine-voice').value;

    btn.disabled = true;
    btn.textContent = '合成中...';

    try {
        const resp = await fetch('/api/test/tts', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                provider: 'volcengine',
                text: text,
                voice_type: voice
            })
        });

        const data = await resp.json();

        if (data.success) {
            const audio = document.getElementById('tts-audio-player');
            audio.src = data.audio_url;
            document.getElementById('tts-test-result').classList.remove('hidden');
        } else {
            alert('测试失败: ' + data.error);
        }
    } finally {
        btn.disabled = false;
        btn.textContent = '测试语音合成';
    }
});
```

---

## 📋 模块影响清单

### 新增文件 (4个)

```
src/translation/
├── __init__.py              # ~20 行
├── base.py                  # ~30 行
├── volcengine_ark.py        # ~100 行
└── llm_translator.py        # ~150 行 (重构)
```

### 修改文件 (4个)

```
src/tts/volcengine_tts.py    # 适配 HTTP API + 音色
src/production/pipeline.py   # 集成翻译路由
web/templates/settings.html  # 新增配置区 + 测试按钮
web/app.py                   # 新增测试接口
config/settings.yaml         # 新增配置段
```

---

## ⚙️ 配置设计

### config/settings.yaml

```yaml
# 翻译配置
translation:
  provider: "volcengine_ark"  # volcengine_ark / llm

  volcengine_ark:
    enabled: true
    base_url: "https://ark.cn-beijing.volces.com/api/v3"
    api_key: ""
    model: "doubao-seed-translation"
    timeout: 60

  llm:  # 备选
    base_url: "https://api.groq.com/openai/v1"
    api_key: ""
    model: "llama-3.3-70b-versatile"

# TTS 配置（改进）
tts:
  provider: "volcengine"

  volcengine:
    enabled: true
    appid: ""
    access_token: ""
    cluster: "volcano_tts"
    default_voice: "BV001_streaming"
    available_voices:
      - {id: "BV001_streaming", name: "通用女声"}
      - {id: "BV002_streaming", name: "通用男声"}
      - {id: "BV700_streaming", name: "知性女声"}
      - {id: "BV701_streaming", name: "温柔女声"}
      - {id: "BV702_streaming", name: "活力女声"}
      - {id: "BV705_streaming", name: "磁性男声"}
```

---

## 📅 实施计划

### Day 1: 火山方舟翻译
- [ ] 实现 VolcengineArkTranslator
- [ ] 翻译路由层
- [ ] 配置文件

### Day 2: 火山 TTS 改进
- [ ] 修改 VolcengineTTS 适配 HTTP API
- [ ] 音色配置
- [ ] 配置文件

### Day 3: 前端界面
- [ ] 翻译配置区
- [ ] TTS 配置区 + 音色选择
- [ ] 测试按钮

### Day 4: 测试接口
- [ ] 后端测试 API
- [ ] 前端测试逻辑
- [ ] 音频播放

### Day 5: 集成测试
- [ ] 端到端测试
- [ ] 前端测试功能验证

---

## ✅ 完成定义

- [ ] 火山方舟翻译可用
- [ ] 火山引擎 TTS 可用（HTTP API）
- [ ] 前端可配置所有参数
- [ ] 前端可选择音色
- [ ] 测试功能可快速验证（翻译 + TTS）
- [ ] 不影响现有功能


---

## 🧪 模块 4: 快速测试接口

### 后端 API

```python
# web/app.py

@app.post("/api/test/translation")
async def test_translation(request: Request):
    """测试翻译功能"""
    data = await request.json()
    provider = data.get("provider", "volcengine_ark")
    text = data.get("text", "Hello world")
    target_lang = data.get("target_lang", "zh")

    try:
        if provider == "volcengine_ark":
            translator = VolcengineArkTranslator(config)
        else:
            translator = LLMTranslator(config)

        result = await translator.translate(text, target_lang=target_lang)

        return {
            "success": True,
            "result": result,
            "provider": provider
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/api/test/tts")
async def test_tts(request: Request):
    """测试 TTS 功能"""
    data = await request.json()
    provider = data.get("provider", "volcengine")
    text = data.get("text", "你好世界")
    voice_type = data.get("voice_type", "BV001_streaming")

    try:
        if provider == "volcengine":
            tts = VolcengineTTS(config)
        else:
            # KlicStudio TTS
            return {"success": False, "error": "KlicStudio 测试暂不支持"}

        # 生成临时文件
        output_path = f"/tmp/test_tts_{uuid.uuid4()}.mp3"
        result = await tts.synthesize(text, output_path, voice_type=voice_type)

        if result:
            # 返回音频 URL
            return {
                "success": True,
                "audio_url": f"/api/audio/{Path(output_path).name}",
                "voice_type": voice_type
            }
        else:
            return {"success": False, "error": "TTS 合成失败"}

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/api/audio/{filename}")
async def serve_audio(filename: str):
    """提供音频文件"""
    file_path = f"/tmp/{filename}"
    if Path(file_path).exists():
        return FileResponse(file_path, media_type="audio/mpeg")
    return {"error": "File not found"}


@app.get("/api/tts/voices")
async def get_tts_voices():
    """获取可用音色列表"""
    config = Config()
    voices = config.get("tts.volcengine.available_voices", default=[])
    return {"voices": voices}
```

---

## 📊 数据流设计

### 完整流程

```
视频输入
  ↓
ASR (YouTube/Whisper/火山ASR)
  ↓
原文字幕 SRT
  ↓
翻译路由
  ├─ 火山方舟翻译 (优先)
  └─ LLM 翻译 (备选)
  ↓
翻译字幕 SRT
  ↓
TTS 路由
  ├─ 火山引擎 TTS (选择音色)
  └─ KlicStudio TTS (备选)
  ↓
配音音频
  ↓
视频合成
```

### 测试流程

```
前端配置界面
  ↓
点击"测试翻译"
  ↓
POST /api/test/translation
  ↓
返回翻译结果
  ↓
前端显示结果

前端配置界面
  ↓
选择音色 + 点击"测试语音合成"
  ↓
POST /api/test/tts
  ↓
返回音频 URL
  ↓
前端播放音频
```

---

## 📋 模块影响清单

### 新增文件 (4个)

```
src/translation/
├── __init__.py              # ~30 行
├── base.py                  # ~40 行
├── volcengine_ark.py        # ~100 行
└── llm_translator.py        # ~80 行 (重构)
```

### 修改文件 (4个)

```
src/tts/volcengine_tts.py
- 修改为 HTTP API 调用
- 新增音色参数支持

src/production/pipeline.py
- 集成翻译路由层

web/templates/settings.html
- 新增火山方舟翻译配置区
- 新增火山 TTS 音色选择
- 新增测试按钮

web/app.py
- 新增 /api/test/translation
- 新增 /api/test/tts
- 新增 /api/tts/voices
- 新增 /api/audio/{filename}
```

### 配置变更

```yaml
config/settings.yaml:
- 新增 translation 配置段
- 修改 tts.volcengine 配置（新增音色列表）
```

---

## ⚙️ 完整配置设计

### config/settings.yaml

```yaml
# 翻译配置
translation:
  provider: "volcengine_ark"  # volcengine_ark / llm

  volcengine_ark:
    enabled: true
    base_url: "https://ark.cn-beijing.volces.com/api/v3"
    api_key: ""
    model: "doubao-seed-translation"
    timeout: 60

  llm:
    # 现有 LLM 配置保持不变
    base_url: "https://api.groq.com/openai/v1"
    api_key: ""
    model: "llama-3.3-70b-versatile"

# TTS 配置（修改）
tts:
  provider: "volcengine"  # volcengine / klicstudio

  volcengine:
    enabled: true
    appid: ""
    access_token: ""
    cluster: "volcano_tts"
    api_url: "https://openspeech.bytedance.com/api/v1/tts"
    default_voice: "BV001_streaming"
    timeout: 120

    # 可选音色列表
    available_voices:
      - id: "BV001_streaming"
        name: "通用女声"
        language: "zh-CN"
        description: "适合通用场景"
      - id: "BV002_streaming"
        name: "通用男声"
        language: "zh-CN"
        description: "适合通用场景"
      - id: "BV700_streaming"
        name: "知性女声"
        language: "zh-CN"
        description: "适合知识类内容"
      - id: "BV701_streaming"
        name: "温柔女声"
        language: "zh-CN"
        description: "适合温馨场景"
      - id: "BV702_streaming"
        name: "活力女声"
        language: "zh-CN"
        description: "适合活力场景"
      - id: "BV705_streaming"
        name: "磁性男声"
        language: "zh-CN"
        description: "适合深沉场景"
```

---

## 🚨 错误处理

### 降级策略

```python
# 1. 火山方舟翻译失败 → 降级到 LLM 翻译
if volcengine_ark_failed:
    fallback_to_llm_translator()

# 2. 火山引擎 TTS 失败 → 降级到 KlicStudio
if volcengine_tts_failed:
    fallback_to_klicstudio_tts()

# 3. 测试接口异常 → 返回详细错误信息
try:
    result = await test_function()
except Exception as e:
    return {"success": False, "error": str(e), "traceback": traceback.format_exc()}
```

---

## 📅 实施计划

### Day 1: 火山方舟翻译 (后端)
- [ ] 创建 `src/translation/` 模块
- [ ] 实现 `VolcengineArkTranslator`
- [ ] 重构现有翻译逻辑为 `LLMTranslator`
- [ ] 集成到 `ProductionPipeline`

### Day 2: 火山方舟翻译 (前端 + 测试)
- [ ] 前端配置界面
- [ ] 实现 `/api/test/translation`
- [ ] 测试翻译功能

### Day 3: 火山引擎 TTS (后端)
- [ ] 修改 `VolcengineTTS` 为 HTTP API
- [ ] 新增音色参数支持
- [ ] 配置音色列表

### Day 4: 火山引擎 TTS (前端 + 测试)
- [ ] 前端音色选择下拉框
- [ ] 实现 `/api/test/tts`
- [ ] 实现 `/api/tts/voices`
- [ ] 实现 `/api/audio/{filename}`
- [ ] 测试 TTS 功能

### Day 5: 集成测试
- [ ] 端到端测试
- [ ] 降级策略测试
- [ ] 前端测试功能验证

---

## ✅ 完成定义

- [ ] 火山方舟翻译后端实现
- [ ] 火山引擎 TTS HTTP API 实现
- [ ] 音色选择功能
- [ ] 前端配置界面完整
- [ ] 测试接口可用
- [ ] 端到端测试通过
- [ ] 降级策略验证通过

**下一步**: 进入 Step 3 实施

