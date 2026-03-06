# Step 1: Requirements - 二次创作引擎

**创建时间**: 2026-03-04 16:00
**需求提出者**: 开发者
**需求澄清者**: Claude
**状态**: 需求澄清中

---

## 需求背景

当前 VideoFactory 的"二次创作"能力非常基础，主要是技术性的辅助工具：
- 长视频加工：烧录字幕（技术处理）
- 短切片：基于时间戳机械裁剪
- 图文生成：简单的 LLM 调用

**真正的"二次创作"应该是**：基于原始内容，通过 AI 智能分析和创意加工，生成新的、有价值的内容产品。

---

## 核心需求

### 1. 目标愿景
构建一个 **AI 驱动的视频二次创作引擎**，能够：
- 智能分析视频内容
- 自动识别知识点/高光片段
- 按照预设模板自动成片
- 支持多素材混剪融合
- 生成符合平台风格的短视频

### 2. 主要应用场景
**知识/教程视频的切片分发**

**典型流程**：
```
长教程视频（60分钟）
    ↓
AI 分析识别 10 个知识点
    ↓
每个知识点生成独立短视频（1-3分钟）
    ↓
自动添加片头/片尾/字幕/BGM/特效
    ↓
输出 10 个可直接发布的短视频
```

### 3. 自动化程度
**模板驱动 + 全自动生成**
- 预先配置各类创作模板（风格、元素、时长等）
- 输入视频后，自动按模板完成所有创作步骤
- 无需人工介入（或仅需审核环节）

---

## 核心能力详细需求

### 能力 1: AI 智能剪辑（识别高光、自动成片）

#### 1.1 知识点识别
**输入**: 长视频 + 字幕文件（翻译后的 SRT）

**识别策略**: **AI 分析字幕内容**
- 使用 LLM 分析字幕文本
- 识别话题切换点（如"现在讲第二点"、"接下来介绍"）
- 识别语义边界（段落结束、总结陈述）

**切分策略**: **混合策略**
- 优先按话题边界自然切分
- 但不超过最大时长限制（可配置，如 3 分钟）
- 不低于最小时长限制（可配置，如 30 秒）

**输出**:
```python
[
  {
    "segment_id": "seg_001",
    "title": "什么是Docker",
    "start_time": 0.0,
    "end_time": 125.5,
    "duration": 125.5,
    "summary": "介绍Docker的基本概念和应用场景",
    "keywords": ["Docker", "容器化", "虚拟化"]
  },
  {
    "segment_id": "seg_002",
    "title": "Docker与虚拟机的区别",
    "start_time": 125.5,
    "end_time": 280.0,
    "duration": 154.5,
    "summary": "对比Docker容器和传统虚拟机的优缺点",
    "keywords": ["Docker", "虚拟机", "对比"]
  },
  ...
]
```

#### 1.2 高光识别（未来扩展）
**当前优先级**: P2（第二期）

**识别维度**:
- 音量峰值（掌声、笑声）
- 画面变化（PPT 切换、演示操作）
- 语速变化（强调重点时语速变慢）
- 情感分析（关键结论、重要观点）

---

### 能力 2: 混剪能力（多素材融合）

#### 2.1 同系列视频融合
**场景**: 多期教程提取同一知识点，混剪成精华版

**示例**:
```
视频A（Python入门 - 第1期）: 讲解变量定义（2分钟）
视频B（Python入门 - 第2期）: 再次讲解变量（补充细节，3分钟）
视频C（Python进阶 - 第5期）: 变量的最佳实践（2分钟）
    ↓
混剪输出：《Python变量完全指南》（7分钟精华版）
```

**匹配策略**: **AI 语义匹配**
- 分析每个片段的字幕内容
- LLM 判断是否在讲同一个知识点
- 根据语义相似度评分排序

**融合规则**:
```python
{
  "topic": "Python变量定义",
  "source_segments": [
    {"video": "Python入门-01", "segment": "seg_002", "score": 0.95},
    {"video": "Python入门-02", "segment": "seg_003", "score": 0.92},
    {"video": "Python进阶-05", "segment": "seg_001", "score": 0.88}
  ],
  "fusion_strategy": "sequential",  # 顺序拼接
  "max_duration": 300,  # 最长5分钟
  "priority": "score_desc"  # 按相似度降序
}
```

**输出**: 一个新的混剪短视频

#### 2.2 其他混剪场景（未来扩展）
- 对比类视频（P3）
- 合集类视频（P3）

---

### 能力 3: 解说配音（AI 口播）

#### 3.1 全程 AI 解说配音
**流程**:
```
1. 提取原视频关键片段
2. 基于字幕生成解说文案（LLM）
3. 文案转语音（Edge-TTS 或其他 TTS）
4. 替换原始音频 + 降低原音频音量作为背景
5. 输出新视频
```

**解说文案生成**:
- 输入：字幕文本 + 片段时长
- LLM 任务：
  - 提炼核心观点
  - 改写为口语化解说词
  - 控制字数（符合片段时长，如 150字/分钟）
  - 风格：专业、简洁、吸引人

**示例**:
```
原字幕（100字，语速快）:
"Docker是一个开源的容器化平台，它允许开发者将应用程序及其依赖项打包成一个轻量级、可移植的容器..."

解说文案（60字，语速适中）:
"Docker，一个强大的容器化工具。简单来说，它能把你的应用和环境打包在一起，随时随地运行，再也不用担心环境配置问题。"
```

**音频处理**:
- TTS 引擎：Edge-TTS（免费）或其他
- 音色选择：支持多种音色（男声/女声，正式/活泼）
- 音量混合：
  - 解说配音音量：100%
  - 原视频音频：降低到 20-30%（作为背景音）

#### 3.2 简短引导语（未来扩展）
**场景**: 片头加"这期讲XXX"，片尾加"喜欢记得关注"

**优先级**: P2

---

### 能力 4: 创意特效（转场、滤镜、动画）

#### 4.1 片头/片尾生成
**方式**: **模板化（推荐）**

**片头模板结构**:
```
[3秒片头视频]
- 背景：渐变动画或纯色
- Logo: 频道 Logo（PNG 透明底）
- 标题文字：知识点标题（AI 生成或提取）
  - 字体：可配置
  - 动画：飞入、渐显、缩放等
- BGM: 轻快的片头音乐（3秒）
```

**片尾模板结构**:
```
[5秒片尾视频]
- 文字：
  - "喜欢记得一键三连"
  - "关注我，持续学习XX知识"
- 二维码/关注按钮动画
- BGM: 欢快的片尾音乐（5秒）
```

**配置示例**:
```yaml
intro_template:
  duration: 3  # 秒
  background:
    type: "gradient"  # gradient / solid / video
    colors: ["#4A90E2", "#9013FE"]
  logo:
    path: "assets/logo.png"
    position: "center"
    size: "medium"
  title:
    font: "PingFang SC"
    size: 48
    color: "#FFFFFF"
    animation: "fade_in"
  bgm:
    path: "assets/intro_music.mp3"
    volume: 0.3
```

#### 4.2 转场效果
**优先级**: P1（基础转场）

**支持的转场类型**:
- 淡入淡出（Fade）
- 交叉淡化（Crossfade）
- 推送（Push）
- 擦除（Wipe）

**应用场景**:
- 片头 → 正文
- 正文 → 片尾
- 混剪时，片段 A → 片段 B

**配置**:
```yaml
transitions:
  intro_to_content: "fade"  # 片头到正文
  content_to_outro: "crossfade"  # 正文到片尾
  segment_to_segment: "push"  # 混剪片段间
  duration: 0.5  # 转场时长（秒）
```

#### 4.3 文字动画（未来扩展）
**场景**: 关键字句的文字特效（放大、高亮、飞入）

**优先级**: P2

#### 4.4 调色滤镜（未来扩展）
**场景**: 自动调整画面风格（清新、复古、赛博朋克）

**优先级**: P3

#### 4.5 贴纸/表情包（未来扩展）
**场景**: 根据内容自动添加贴纸、表情包

**优先级**: P3

---

### 能力 5: 短视频元素自动化

#### 5.1 字幕样式
**需求**: 根据平台风格自动选择字幕样式

**平台风格映射**:
```python
{
  "douyin": {
    "style": "弹幕风",
    "font": "思源黑体",
    "font_size": 36,
    "color": "#FFFF00",  # 黄色
    "outline": True,
    "outline_color": "#000000",
    "position": "bottom_center",
    "animation": "bounce"  # 弹跳效果
  },
  "bilibili": {
    "style": "标准字幕",
    "font": "思源黑体",
    "font_size": 32,
    "color": "#FFFFFF",
    "outline": True,
    "outline_color": "#000000",
    "position": "bottom_center",
    "animation": "none"
  },
  "xiaohongshu": {
    "style": "大字幕",
    "font": "站酷快乐体",
    "font_size": 40,
    "color": "#FF6B9D",  # 粉色
    "outline": True,
    "outline_color": "#FFFFFF",
    "position": "center",
    "animation": "fade"
  }
}
```

#### 5.2 BGM 自动匹配
**需求**: 根据视频内容类型，自动选择合适的 BGM

**BGM 库分类**:
```python
{
  "educational": [  # 教程类
    "bgm/light_piano_01.mp3",
    "bgm/soft_guitar_02.mp3"
  ],
  "energetic": [  # 激励类
    "bgm/upbeat_electronic_01.mp3",
    "bgm/motivational_01.mp3"
  ],
  "calm": [  # 平静类
    "bgm/ambient_01.mp3",
    "bgm/meditation_01.mp3"
  ]
}
```

**匹配策略**:
- 输入知识点的 `keywords` 和 `summary`
- LLM 判断内容类型（教程/激励/平静）
- 从对应分类随机选择 BGM

**音量控制**:
- BGM 音量：20-30%（不盖过解说）
- 原视频音频：0%（如果有 AI 解说）或 100%（如果保留原音）

---

## 模板系统设计

### 模板结构
```yaml
template_name: "知识教程-抖音风格"
platform: "douyin"  # 目标平台
orientation: "vertical"  # vertical / horizontal
aspect_ratio: "9:16"

# 内容识别配置
content_analysis:
  segmentation:
    strategy: "hybrid"  # natural / fixed / hybrid
    min_duration: 30  # 秒
    max_duration: 180  # 秒
  highlight_detection: false  # 当前不启用

# 片头配置
intro:
  enabled: true
  template_path: "templates/intro_douyin.mp4"
  duration: 3
  title_overlay:
    enabled: true
    font: "PingFang SC"
    size: 48
    color: "#FFFFFF"
    animation: "bounce"

# 片尾配置
outro:
  enabled: true
  template_path: "templates/outro_douyin.mp4"
  duration: 5

# 字幕配置
subtitle:
  style: "danmu"  # 弹幕风
  font: "思源黑体"
  font_size: 36
  color: "#FFFF00"
  outline: true
  outline_color: "#000000"
  position: "bottom_center"
  animation: "bounce"

# 转场配置
transitions:
  intro_to_content: "fade"
  content_to_outro: "crossfade"
  segment_to_segment: "push"
  duration: 0.5

# BGM 配置
bgm:
  enabled: true
  category: "educational"  # 从 BGM 库中选择类别
  volume: 0.25
  fade_in: 1.0  # 秒
  fade_out: 1.0

# AI 配音配置
voiceover:
  enabled: true  # 全程 AI 解说
  tts_engine: "edge-tts"
  voice: "zh-CN-XiaoxiaoNeural"  # 女声，活泼
  speed: 1.0
  pitch: 0
  original_audio_volume: 0.2  # 原音频作为背景音

# 混剪配置
remix:
  enabled: true  # 启用同系列混剪
  matching_strategy: "semantic"  # semantic / keyword
  max_sources: 3  # 最多混剪3个来源
  fusion_strategy: "sequential"  # sequential / interleaved
```

### 模板库示例
```
templates/
├── douyin_educational.yaml       # 抖音-教程风格
├── douyin_energetic.yaml         # 抖音-激励风格
├── bilibili_standard.yaml        # B站-标准风格
├── xiaohongshu_cute.yaml         # 小红书-可爱风格
├── youtube_professional.yaml     # YouTube-专业风格
└── custom/                       # 用户自定义模板
    └── my_template.yaml
```

---

## 验收标准

### 功能验收
- [ ] **智能剪辑**: 输入60分钟教程视频，自动识别并切分成 5-10 个知识点短视频
- [ ] **片头片尾**: 每个短视频自动添加模板化的片头（3秒）和片尾（5秒）
- [ ] **字幕样式**: 根据目标平台，自动应用对应的字幕样式
- [ ] **转场效果**: 片头→正文→片尾之间有流畅的转场动画
- [ ] **AI 解说**: 自动生成解说文案并配音，替换原视频音频
- [ ] **BGM 匹配**: 自动选择合适的背景音乐，音量适中
- [ ] **同系列混剪**: 给定3个视频，自动识别相同知识点并混剪成精华版
- [ ] **模板系统**: 支持 YAML 配置模板，一键切换风格

### 性能验收
- [ ] 60分钟视频分析 + 切分：< 5 分钟
- [ ] 单个短视频生成（包含所有元素）：< 2 分钟/条
- [ ] 混剪视频生成：< 5 分钟/条

### 质量验收
- [ ] 知识点识别准确率：> 85%
- [ ] 语义匹配准确率（混剪）：> 80%
- [ ] 解说文案流畅度：人工评估 > 4分/5分
- [ ] 字幕同步准确度：误差 < 0.5秒

---

## 技术债务和风险

### 技术债务
1. **AI 能力依赖**: 依赖 LLM 的语义理解能力，需要调优 prompt
2. **素材库管理**: 片头/片尾/BGM 素材需要统一管理和版权清理
3. **性能优化**: 视频处理耗时较长，需要考虑并发和缓存

### 风险
1. **LLM 识别不准**: 知识点边界识别可能不准确
   - **缓解**: 提供人工微调接口
2. **TTS 音质**: 免费 TTS 音质可能不够自然
   - **缓解**: 支持多种 TTS 引擎切换
3. **混剪语义匹配**: 不同视频讲法差异大，匹配困难
   - **缓解**: 降低相似度阈值，扩大候选范围

---

## 优先级和迭代计划

### P0 - MVP 核心（第一期，2-3周）
- ✅ AI 智能剪辑：知识点识别 + 自动切分
- ✅ 片头片尾：模板化拼接
- ✅ 字幕样式：平台风格映射
- ✅ 基础转场：淡入淡出
- ✅ 模板系统：YAML 配置

### P1 - 增强功能（第二期，2-3周）
- ✅ AI 解说配音：文案生成 + TTS
- ✅ BGM 自动匹配
- ✅ 同系列混剪：语义匹配 + 融合
- ✅ 更多转场效果

### P2 - 智能化升级（第三期，3-4周）
- 高光识别（音量、画面、情感）
- 文字动画（关键字句特效）
- 简短引导语（片头/片尾）

### P3 - 高级特性（第四期，按需）
- 调色滤镜
- 贴纸/表情包
- 对比类/合集类混剪

---

## 下一步

**等待开发者确认**:
1. 需求是否明确？
2. 优先级是否认可？
3. 验收标准是否合理？

**确认后进入 Step 2 (设计方案)**。
