# Step 1: Requirements - 视频智能切片与二次创作系统（基于现成工具）

**创建时间**: 2026-03-04 16:30
**需求提出者**: 开发者
**需求澄清者**: Claude
**状态**: 需求明确，技术调研完成

---

## 📋 核心需求（精简版）

### 输入
一个 YouTube 长视频（如 Antido 的书籍知识科普视频）

### 处理流程
```
长视频（30-60分钟）
    ↓
AI 智能切分成 5-10 个知识点片段
    ↓
每个片段添加：
  - 转场动画
  - AI 解说配音
  - 片头片尾
  - 字幕样式
    ↓
输出 5-10 个可直接发布的短视频
```

### 输出
- 抖音版本（9:16 竖屏，弹幕风字幕）
- 小红书版本（9:16 竖屏，大字幕）
- B站版本（16:9 横屏，标准字幕）

---

## 🔍 技术调研结果

### 方案对比

| 方案 | 类型 | 优势 | 劣势 | 推荐度 |
|------|------|------|------|--------|
| **OpusClip** | 商业SaaS | 成熟稳定，开箱即用，AI识别准确 | 付费，不可定制 | ⭐⭐⭐⭐ |
| **Vizard.ai** | 商业SaaS | 教育内容优化，字幕精准 | 付费，不可定制 | ⭐⭐⭐⭐ |
| **AutoShorts (开源)** | GitHub开源 | 免费，可定制 | 需要自己部署和调优 | ⭐⭐⭐ |
| **Clips AI (Python)** | Python库 | 免费，可集成 | 功能基础，需要二次开发 | ⭐⭐⭐ |
| **自研方案** | 自建 | 完全可控 | 开发周期长（3-4个月） | ⭐⭐ |

---

## 💡 推荐方案：混合架构

### 核心思路
**不要重复造轮子，组合现成工具**

```
┌─────────────────────────────────────────────────────────┐
│  VideoFactory 编排层（我们开发）                         │
│  ├─ 任务管理                                             │
│  ├─ 模板配置                                             │
│  └─ 多平台分发                                           │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│  AI 切片引擎（集成现成工具）                             │
│  ├─ 方案1: OpusClip API（推荐）                         │
│  ├─ 方案2: Clips AI (Python库)                          │
│  └─ 方案3: PySceneDetect + LLM 自研                     │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│  后处理层（我们开发）                                    │
│  ├─ 片头片尾拼接（FFmpeg）                               │
│  ├─ 转场效果（FFmpeg）                                   │
│  ├─ 字幕样式（平台适配）                                 │
│  ├─ AI 配音（Edge-TTS）                                  │
│  └─ BGM 混音（FFmpeg）                                   │
└─────────────────────────────────────────────────────────┘
```

---

## 🎯 详细方案设计

### 阶段1: AI 智能切片（集成现成工具）

#### 方案A: OpusClip API 集成（推荐）⭐⭐⭐⭐⭐

**为什么选择 OpusClip**:
- ✅ 专门针对教育/知识类内容优化
- ✅ AI 识别准确率高（> 90%）
- ✅ 提供 API，可编程集成
- ✅ 自动识别"病毒式"片段（高光时刻）
- ✅ 支持多语言字幕

**集成方式**:
```python
# 伪代码
import opusclip

# 1. 提交视频到 OpusClip
job = opusclip.create_job(
    video_url="https://youtube.com/watch?v=...",
    clip_count=10,  # 生成10个片段
    min_duration=30,  # 最短30秒
    max_duration=180,  # 最长3分钟
    language="zh"
)

# 2. 等待处理完成
clips = opusclip.wait_for_completion(job.id)

# 3. 下载切片结果
for clip in clips:
    download_video(clip.url, f"clip_{clip.id}.mp4")
    # clip.title: AI生成的标题
    # clip.transcript: 字幕文本
    # clip.virality_score: 病毒式评分
```

**成本**: 约 $0.10-0.30/分钟视频

#### 方案B: Clips AI (Python库) - 开源免费⭐⭐⭐⭐

**为什么选择 Clips AI**:
- ✅ 完全免费开源
- ✅ 基于 WhisperX 转录，准确度高
- ✅ 可本地运行，数据隐私
- ✅ 支持自动重构画面（竖屏适配）

**集成方式**:
```python
from clipsai import ClipFinder, Transcriber

# 1. 转录视频
transcriber = Transcriber()
transcript = transcriber.transcribe("video.mp4")

# 2. 识别片段
clip_finder = ClipFinder()
clips = clip_finder.find_clips(
    transcript=transcript,
    min_duration=30,
    max_duration=180
)

# 3. 提取片段
for i, clip in enumerate(clips):
    extract_clip("video.mp4", clip.start, clip.end, f"clip_{i}.mp4")
```

**成本**: 免费（需要本地GPU加速）

#### 方案C: 自研方案（PySceneDetect + LLM）⭐⭐⭐

**仅在以下情况考虑**:
- OpusClip 不支持的特殊需求
- 预算极度有限
- 需要完全自主可控

**技术栈**:
```python
# 1. 场景检测
from scenedetect import detect, ContentDetector
scenes = detect("video.mp4", ContentDetector())

# 2. 字幕分析（LLM）
transcript = extract_subtitles("video.srt")
segments = llm_analyze_knowledge_points(transcript)

# 3. 合并场景和语义边界
clips = merge_scenes_and_segments(scenes, segments)
```

**开发周期**: 2-3周

---

### 阶段2: 后处理（我们开发）

#### 2.1 片头片尾拼接

**技术**: FFmpeg concat

```python
def add_intro_outro(clip_path, intro_path, outro_path, output_path):
    """
    拼接片头、正文、片尾
    """
    ffmpeg.concat([intro_path, clip_path, outro_path], output_path)
```

**片头模板**:
- 3秒动画
- 显示知识点标题（AI生成或提取）
- 频道 Logo

**片尾模板**:
- 5秒动画
- "喜欢记得一键三连"
- 关注引导

#### 2.2 转场效果

**技术**: FFmpeg xfade 滤镜

```python
def add_transitions(clips, output_path):
    """
    在片段间添加转场
    """
    transitions = ["fade", "wipeleft", "circleopen"]
    ffmpeg.xfade(clips, transitions, duration=0.5, output=output_path)
```

**支持的转场**:
- fade（淡入淡出）
- wipeleft（左擦除）
- circleopen（圆形展开）

#### 2.3 字幕样式适配

**技术**: FFmpeg subtitles 滤镜 + ASS 样式

```python
PLATFORM_SUBTITLE_STYLES = {
    "douyin": {
        "font": "思源黑体",
        "font_size": 36,
        "color": "&H00FFFF",  # 黄色
        "outline": 2,
        "position": "bottom_center",
        "animation": "bounce"
    },
    "xiaohongshu": {
        "font": "站酷快乐体",
        "font_size": 40,
        "color": "&H9D6BFF",  # 粉色
        "outline": 2,
        "position": "center"
    },
    "bilibili": {
        "font": "思源黑体",
        "font_size": 32,
        "color": "&HFFFFFF",  # 白色
        "outline": 1,
        "position": "bottom_center"
    }
}

def apply_subtitle_style(video_path, subtitle_path, platform, output_path):
    style = PLATFORM_SUBTITLE_STYLES[platform]
    ass_subtitle = convert_to_ass(subtitle_path, style)
    ffmpeg.burn_subtitle(video_path, ass_subtitle, output_path)
```

#### 2.4 AI 解说配音

**技术**: Edge-TTS（免费）或 OpenAI TTS

```python
from edge_tts import Communicate

async def generate_voiceover(text, output_path):
    """
    生成 AI 配音
    """
    communicate = Communicate(
        text=text,
        voice="zh-CN-XiaoxiaoNeural",  # 女声
        rate="+0%",  # 语速
        pitch="+0Hz"  # 音调
    )
    await communicate.save(output_path)

# 使用流程
# 1. LLM 生成解说文案
script = llm_generate_script(clip_transcript)

# 2. TTS 生成音频
await generate_voiceover(script, "voiceover.mp3")

# 3. 替换原音频
ffmpeg.replace_audio("clip.mp4", "voiceover.mp3", "output.mp4")
```

#### 2.5 BGM 混音

**技术**: FFmpeg amix 滤镜

```python
def add_bgm(video_path, bgm_path, output_path, bgm_volume=0.2):
    """
    添加背景音乐
    """
    ffmpeg.mix_audio(
        video_path,
        bgm_path,
        output_path,
        video_volume=1.0,
        bgm_volume=bgm_volume
    )
```

---

## 📐 模板系统设计

### 模板配置（YAML）

```yaml
template_name: "知识科普-抖音风格"
platform: "douyin"
aspect_ratio: "9:16"

# AI 切片配置
clipping:
  engine: "opusclip"  # opusclip / clipsai / custom
  clip_count: 10
  min_duration: 30
  max_duration: 180

# 片头配置
intro:
  enabled: true
  template_path: "templates/intro_douyin.mp4"
  duration: 3
  title_overlay: true

# 片尾配置
outro:
  enabled: true
  template_path: "templates/outro_douyin.mp4"
  duration: 5

# 转场配置
transitions:
  intro_to_content: "fade"
  content_to_outro: "fade"
  duration: 0.5

# 字幕配置
subtitle:
  style: "douyin"  # 引用预设样式
  burn: true  # 烧录到视频

# AI 配音配置
voiceover:
  enabled: true
  engine: "edge-tts"
  voice: "zh-CN-XiaoxiaoNeural"
  replace_original: true  # 替换原音频
  original_volume: 0.2  # 原音作为背景

# BGM 配置
bgm:
  enabled: true
  path: "assets/bgm/educational_01.mp3"
  volume: 0.25
```

---

## 🚀 实现优先级

### P0 - MVP（2周）⭐⭐⭐⭐⭐

**目标**: 实现基础的视频切片和后处理

**功能**:
- ✅ 集成 OpusClip API 或 Clips AI
- ✅ 片头片尾拼接
- ✅ 基础转场（fade）
- ✅ 字幕样式适配（3个平台）
- ✅ 模板系统（YAML配置）

**验收标准**:
- 输入1个60分钟视频
- 输出10个短视频（抖音/小红书/B站各10个）
- 每个视频包含片头+正文+片尾+字幕

**工作量**: 约80小时

### P1 - 增强（1-2周）⭐⭐⭐⭐

**功能**:
- ✅ AI 解说配音（Edge-TTS）
- ✅ BGM 自动混音
- ✅ 更多转场效果（wipe, circle）
- ✅ 批量处理（多视频并发）

**验收标准**:
- AI 配音流畅自然
- BGM 音量适中
- 支持同时处理5个视频

**工作量**: 约40小时

### P2 - 智能化（2-3周）⭐⭐⭐

**功能**:
- ✅ LLM 优化解说文案
- ✅ 智能 BGM 匹配（根据内容类型）
- ✅ 高光识别（音量/画面分析）
- ✅ 自动标题生成

**验收标准**:
- 解说文案质量评分 > 4/5
- BGM 匹配准确率 > 80%

**工作量**: 约60小时

---

## 💰 成本估算

### 方案A: OpusClip API

**成本**:
- API 费用: $0.20/分钟视频
- 60分钟视频 = $12
- 每月处理100个视频 = $1200/月

**优势**: 零开发成本，立即可用

### 方案B: Clips AI (开源)

**成本**:
- 服务器: GPU 实例 $0.50/小时
- 60分钟视频处理约需10分钟 = $0.08
- 每月处理100个视频 = $8/月

**劣势**: 需要2周开发集成

### 方案C: 自研

**成本**:
- 开发成本: 3-4周 × 40小时/周 = 120-160小时
- 服务器成本: 同方案B

**劣势**: 开发周期长，维护成本高

---

## ✅ 验收标准

### 功能验收
- [ ] 输入YouTube视频URL，自动下载
- [ ] AI 切分成5-10个知识点片段
- [ ] 每个片段自动添加片头（3秒）+ 片尾（5秒）
- [ ] 转场流畅（fade效果）
- [ ] 字幕样式符合平台风格（抖音/小红书/B站）
- [ ] 支持模板配置（YAML）
- [ ] 输出3个平台版本（9:16竖屏 × 2 + 16:9横屏 × 1）

### 性能验收
- [ ] 60分钟视频处理时间 < 30分钟（使用OpusClip）
- [ ] 60分钟视频处理时间 < 60分钟（使用Clips AI）
- [ ] 支持并发处理5个视频

### 质量验收
- [ ] 知识点识别准确率 > 85%（人工抽查）
- [ ] 字幕同步误差 < 0.5秒
- [ ] 转场流畅无卡顿
- [ ] 音频无爆音、无杂音

---

## 🎯 推荐实施路径

### 第一步: 快速验证（1周）

**使用 OpusClip API 快速验证**:
1. 注册 OpusClip 账号
2. 用 Python 调用 API
3. 手动添加片头片尾
4. 验证效果

**目标**: 确认 OpusClip 是否满足需求

### 第二步: 集成到 VideoFactory（1周）

**开发内容**:
1. 创建 `src/creation/` 模块
2. 集成 OpusClip API
3. 实现片头片尾拼接
4. 实现字幕样式适配
5. 实现模板系统

### 第三步: 增强功能（1-2周）

**开发内容**:
1. AI 配音（Edge-TTS）
2. BGM 混音
3. 批量处理

### 第四步: 优化和上线（1周）

**开发内容**:
1. 性能优化
2. 错误处理
3. 监控告警
4. 文档编写

---

## 📚 参考资料

### 商业工具
- [OpusClip](https://opus.pro) - AI视频切片工具
- [Vizard.ai](https://vizard.ai) - 教育内容优化
- [AutoShorts.ai](https://autoshorts.ai) - 自动短视频生成

### 开源工具
- [Clips AI (Python)](https://github.com/Anil-matcha/Clips-AI) - 开源视频切片库
- [PySceneDetect](https://scenedetect.com) - 场景检测
- [MoviePy](https://github.com/Zulko/moviepy) - Python视频编辑

### 技术文档
- [FFmpeg xfade](https://ffmpeg.org/ffmpeg-filters.html#xfade) - 转场滤镜
- [Edge-TTS](https://github.com/rany2/edge-tts) - 免费TTS
- [WhisperX](https://github.com/m-bain/whisperX) - 高精度转录

---

## 下一步

**等待开发者确认**:
1. 是否采用 OpusClip API 方案？（推荐）
2. 还是使用 Clips AI 开源方案？
3. 预算范围是多少？
4. 是否需要调整优先级？

**确认后进入 Step 2 (技术设计)**
