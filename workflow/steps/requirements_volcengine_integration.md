# 需求分解 - 火山引擎翻译+TTS 集成

**创建时间**: 2026-03-04 16:10
**优先级**: P0

---

## 🎯 用户需求

1. **使用火山引擎豆包翻译模型** (`doubao-seed-translation`)
   - 链接: https://console.volcengine.com/ark/region:ark+cn-beijing/model/detail?Id=doubao-seed-translation

2. **使用火山引擎语音合成** (TTS)
   - 文档: https://www.volcengine.com/docs/6561/1719100

3. **前端配置界面需求**:
   - 能够配置火山引擎的认证信息
   - 能够选择不同的音色
   - 能够快速测试是否调通（不需要等任务执行）

---

## 📋 需求分解

### 需求 1: 火山方舟翻译集成

**背景**:
- 火山方舟提供 OpenAI 兼容的 API
- `doubao-seed-translation` 是专门的翻译模型
- 需要替换现有的 LLM 翻译

**技术方案**:
```python
# 火山方舟 API (OpenAI 兼容)
base_url = "https://ark.cn-beijing.volces.com/api/v3"
model = "doubao-seed-translation"  # 或 endpoint ID
api_key = "your-api-key"

# 调用方式 (OpenAI SDK)
from openai import OpenAI
client = OpenAI(
    base_url=base_url,
    api_key=api_key
)
response = client.chat.completions.create(
    model=model,
    messages=[
        {"role": "system", "content": "You are a professional translator."},
        {"role": "user", "content": "Translate to Chinese: Hello world"}
    ]
)
```

**配置项**:
```yaml
translation:
  provider: "volcengine_ark"  # 新增
  volcengine_ark:
    base_url: "https://ark.cn-beijing.volces.com/api/v3"
    api_key: ""
    model: "doubao-seed-translation"  # 或 endpoint ID
    timeout: 60
```

---

### 需求 2: 火山引擎 TTS 集成

**背景**:
- 火山引擎提供 HTTP API 语音合成
- 支持多种音色
- 需要 appid + access_token 认证

**技术方案**:
```python
# HTTP API
url = "https://openspeech.bytedance.com/api/v1/tts"
headers = {
    "Authorization": f"Bearer {access_token}"
}
data = {
    "app": {
        "appid": appid,
        "token": "access_token",
        "cluster": "volcano_tts"
    },
    "user": {
        "uid": "user_id"
    },
    "audio": {
        "voice_type": "BV001_streaming",  # 音色
        "encoding": "mp3",
        "speed_ratio": 1.0,
        "volume_ratio": 1.0,
        "pitch_ratio": 1.0
    },
    "request": {
        "reqid": "uuid",
        "text": "要合成的文本",
        "text_type": "plain",
        "operation": "query"
    }
}
```

**常用音色列表**:
```
BV001_streaming - 通用女声
BV002_streaming - 通用男声
BV700_streaming - 知性女声
BV701_streaming - 温柔女声
BV702_streaming - 活力女声
BV705_streaming - 磁性男声
```

**配置项**:
```yaml
tts:
  provider: "volcengine"
  volcengine:
    enabled: true
    appid: ""
    access_token: ""
    cluster: "volcano_tts"
    voice_type: "BV001_streaming"  # 默认音色
    available_voices:  # 可选音色列表
      - id: "BV001_streaming"
        name: "通用女声"
      - id: "BV002_streaming"
        name: "通用男声"
      - id: "BV700_streaming"
        name: "知性女声"
```

---

### 需求 3: 前端配置界面

**新增配置项**:

1. **翻译配置区域**:
   ```
   [ ] 使用火山方舟翻译
   Base URL: [https://ark.cn-beijing.volces.com/api/v3]
   API Key: [********************]
   Model/Endpoint: [doubao-seed-translation]
   [测试连接]
   ```

2. **TTS 配置区域**:
   ```
   [ ] 使用火山引擎 TTS
   App ID: [********************]
   Access Token: [********************]
   Cluster: [volcano_tts]

   默认音色: [下拉选择]
     - 通用女声 (BV001_streaming)
     - 通用男声 (BV002_streaming)
     - 知性女声 (BV700_streaming)
     - ...

   [测试语音合成]
   ```

3. **测试功能**:
   - 翻译测试: 输入英文 → 点击测试 → 显示中文翻译结果
   - TTS 测试: 输入文本 → 选择音色 → 点击测试 → 播放音频

---

### 需求 4: 快速测试接口

**后端 API**:
```python
# 测试翻译
POST /api/test/translation
{
    "provider": "volcengine_ark",
    "text": "Hello world",
    "target_lang": "zh"
}
→ {"result": "你好世界", "success": true}

# 测试 TTS
POST /api/test/tts
{
    "provider": "volcengine",
    "text": "你好世界",
    "voice_type": "BV001_streaming"
}
→ {"audio_url": "/tmp/test.mp3", "success": true}
```

---

## 📊 实施优先级

### Phase 1: 火山方舟翻译 (1-2天)
- [ ] 后端: 实现 VolcengineArkTranslator
- [ ] 配置: 新增 translation.volcengine_ark 配置段
- [ ] 前端: 翻译配置界面
- [ ] 测试: 翻译测试接口

### Phase 2: 火山引擎 TTS (2-3天)
- [ ] 后端: 修改 VolcengineTTS 适配 HTTP API
- [ ] 配置: 新增音色列表配置
- [ ] 前端: TTS 配置界面 + 音色选择
- [ ] 测试: TTS 测试接口

### Phase 3: 集成测试 (1天)
- [ ] 端到端测试
- [ ] 前端测试功能验证

---

## ✅ 完成标准

- [ ] 火山方舟翻译可用
- [ ] 火山引擎 TTS 可用
- [ ] 前端可配置所有参数
- [ ] 前端可选择音色
- [ ] 测试功能可快速验证
- [ ] 不影响现有功能

---

## 🔗 参考资料

- [火山方舟控制台](https://console.volcengine.com/ark/)
- [火山引擎 TTS 文档](https://www.volcengine.com/docs/6561/1719100)
- [火山引擎 HTTP API](https://www.volcengine.com/docs/6489/71999)
- [Agently 豆包配置示例](https://agently.tech/docs/models/doubao.html)

