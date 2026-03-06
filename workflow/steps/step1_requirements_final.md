# Step 1: Requirements - 英文视频到中文短视频的AI切片系统

**创建时间**: 2026-03-04 17:00
**需求提出者**: 开发者
**需求澄清者**: Claude
**状态**: 需求明确

---

## 🎯 核心需求（精简版）

### 产品定位
**英文长视频 → 中文短视频切片系统**

借鉴 OpusClip 的核心能力，但专注于跨语言场景：
- ✅ Long to shorts（AI识别高光片段）
- ✅ Auto reframe（自动适配平台尺寸）
- ✅ 翻译配音（英文→中文）
- ✅ 中文字幕

❌ 不需要：Caption编辑、B-Roll、复杂视频编辑

---

## 📋 完整流程

```
英文YouTube视频（60分钟）
    ↓
【阶段1: 翻译配音】(已有能力)
├─ 下载视频
├─ 提取英文字幕
├─ KlicStudio 翻译成中文
└─ 输出：中文配音视频 + 中文字幕
    ↓
【阶段2: AI智能切片】(新增能力)
├─ AI分析中文字幕，识别5-10个高光片段
├─ 每个片段提取关键帧
└─ 输出：5-10个知识点片段
    ↓
【阶段3: 自动Reframe】(新增能力)
├─ 检测画面主体（人物/PPT/演示）
├─ 智能裁剪成竖屏（9:16）
└─ 保持主体居中
    ↓
【阶段4: 后处理】(新增能力)
├─ 添加片头片尾
├─ 烧录中文字幕（平台风格）
├─ 添加转场效果
└─ 混音BGM
    ↓
输出：5-10个可直接发布的中文短视频
```

---

## 🔑 三大核心能力

### 能力1: Long to Shorts（AI识别高光）

**借鉴 OpusClip 的方法**:
- 分析字幕文本的语义
- 识别"钩子"（hooks）：引人入胜的开场
- 识别"高光"（highlights）：关键观点、精彩片段
- 评分排序，选出Top N片段

**我们的实现方式**:
```python
# 方案A: 使用 OpusClip API（推荐）
clips = opusclip.create_clips(
    video_url="translated_video.mp4",
    language="zh",  # 中文字幕
    clip_count=8
)

# 方案B: 借鉴 autoshorts 的方法（开源）
# 1. 使用 LLM 分析中文字幕
segments = llm_analyze_highlights(
    transcript="中文字幕文本",
    prompt="""
    分析这段视频字幕，识别5-10个最有价值的知识点片段。
    每个片段应该：
    1. 有明确的主题
    2. 包含完整的观点
    3. 时长30-180秒
    4. 有吸引力的开场

    输出JSON格式...
    """
)

# 2. 结合音频/视频特征
audio_peaks = detect_audio_peaks(video)  # 掌声、笑声
scene_changes = detect_scene_changes(video)  # 画面切换

# 3. 综合评分
for segment in segments:
    segment.score = (
        segment.semantic_score * 0.6 +  # 语义重要性
        segment.audio_score * 0.2 +     # 音频特征
        segment.visual_score * 0.2      # 视觉特征
    )
```

### 能力2: Auto Reframe（智能裁剪竖屏）

**借鉴 OpusClip 的方法**:
- 检测画面中的主体（人物、PPT、演示区域）
- 智能裁剪，保持主体居中
- 适配不同平台尺寸（9:16竖屏、16:9横屏）

**我们的实现方式**:
```python
# 方案A: 使用 OpusClip API 的 reframe 功能
opusclip.reframe(
    clip="clip.mp4",
    aspect_ratio="9:16",
    focus="auto"  # 自动检测主体
)

# 方案B: 借鉴 autoshorts 的方法（开源）
from reframe import SmartCrop

# 1. 检测主体
detector = ObjectDetector(model="yolov8")
subjects = detector.detect(video, classes=["person", "screen"])

# 2. 智能裁剪
cropper = SmartCrop(
    input_aspect="16:9",
    output_aspect="9:16"
)

for frame in video:
    # 跟踪主体位置
    subject_bbox = track_subject(frame, subjects)

    # 裁剪，保持主体居中
    cropped_frame = cropper.crop(
        frame,
        focus_bbox=subject_bbox,
        padding=0.1  # 10%边距
    )
```

### 能力3: 翻译配音 + 字幕（已有能力，需整合）

**当前能力**:
- ✅ KlicStudio 翻译配音（英文→中文）
- ✅ 字幕烧录（LongVideoProcessor）

**需要增强**:
- 字幕样式适配不同平台
- 字幕动画效果（弹幕风、渐显等）

---

## 🏗️ 技术架构

### 整体架构

```
┌─────────────────────────────────────────────────────────┐
│  VideoFactory 编排层                                     │
│  ├─ 任务管理（Task）                                     │
│  ├─ 模板配置（Template）                                 │
│  └─ 多平台分发（Distribute）                             │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│  翻译配音层（已有）                                      │
│  ├─ 下载器（Downloader）                                 │
│  ├─ KlicStudio 翻译（KlicStudioClient）                  │
│  └─ 质检（QualityChecker）                               │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│  AI 切片层（新增）                                       │
│  ├─ 高光识别（HighlightDetector）                       │
│  │   - LLM 语义分析                                      │
│  │   - 音频特征检测                                      │
│  │   - 场景变化检测                                      │
│  └─ 片段提取（ClipExtractor）                           │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│  智能裁剪层（新增）                                      │
│  ├─ 主体检测（SubjectDetector）                         │
│  │   - YOLOv8 目标检测                                   │
│  │   - 人脸检测                                          │
│  │   - 屏幕区域检测                                      │
│  └─ 智能裁剪（SmartCropper）                            │
│      - 跟踪主体                                          │
│      - 动态裁剪                                          │
│      - 平滑过渡                                          │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│  后处理层（增强）                                        │
│  ├─ 片头片尾（IntroOutro）                               │
│  ├─ 转场效果（Transitions）                              │
│  ├─ 字幕烧录（SubtitleRenderer）                         │
│  └─ BGM 混音（AudioMixer）                               │
└─────────────────────────────────────────────────────────┘
```

---

## 💡 技术方案选择

### 方案对比

| 能力 | 方案A: OpusClip API | 方案B: 开源自研 | 推荐 |
|------|---------------------|-----------------|------|
| **高光识别** | OpusClip API | LLM + autoshorts算法 | A（快速）|
| **智能裁剪** | OpusClip API | YOLOv8 + 自研算法 | B（可控）|
| **翻译配音** | 已有（KlicStudio） | 已有（KlicStudio） | - |
| **后处理** | 自研（FFmpeg） | 自研（FFmpeg） | - |

### 推荐混合方案

**阶段1: 快速验证（使用OpusClip）**
- 翻译配音：KlicStudio（已有）
- AI切片：OpusClip API
- 智能裁剪：OpusClip API
- 后处理：自研

**阶段2: 降本增效（逐步替换）**
- 翻译配音：KlicStudio（保持）
- AI切片：自研（LLM + autoshorts算法）
- 智能裁剪：自研（YOLOv8）
- 后处理：自研

---

## 📐 模块设计

### 新增模块：src/creation/

```
src/creation/
├── __init__.py
├── highlight_detector.py    # 高光识别
├── clip_extractor.py        # 片段提取
├── subject_detector.py      # 主体检测
├── smart_cropper.py         # 智能裁剪
├── intro_outro.py           # 片头片尾
├── transitions.py           # 转场效果
└── pipeline.py              # 创作管线
```

### 核心类设计

```python
# highlight_detector.py
class HighlightDetector:
    """高光片段识别器"""

    def __init__(self, method="llm"):
        """
        Args:
            method: "llm" | "opusclip" | "hybrid"
        """
        self.method = method

    async def detect(
        self,
        video_path: str,
        transcript: str,
        clip_count: int = 8,
        min_duration: int = 30,
        max_duration: int = 180
    ) -> List[Segment]:
        """
        识别高光片段

        Returns:
            [
                {
                    "start": 125.5,
                    "end": 280.0,
                    "title": "什么是复利效应",
                    "score": 0.92,
                    "keywords": ["复利", "习惯"]
                },
                ...
            ]
        """
        pass

# smart_cropper.py
class SmartCropper:
    """智能裁剪器"""

    def __init__(self, method="yolo"):
        """
        Args:
            method: "yolo" | "opusclip" | "simple"
        """
        self.method = method

    async def crop(
        self,
        video_path: str,
        output_aspect: str = "9:16",
        focus: str = "auto"
    ) -> str:
        """
        智能裁剪视频

        Args:
            focus: "auto" | "center" | "face" | "screen"
        """
        pass
```

---

## 🚀 实施计划

### P0 - MVP（3周）

**Week 1: 集成 OpusClip**
- [ ] 注册 OpusClip API
- [ ] 实现 HighlightDetector（OpusClip方式）
- [ ] 实现 SmartCropper（OpusClip方式）
- [ ] 测试：英文视频 → 翻译 → 切片 → 裁剪

**Week 2: 后处理层**
- [ ] 实现片头片尾拼接
- [ ] 实现转场效果
- [ ] 实现字幕样式适配（3个平台）
- [ ] 实现 BGM 混音

**Week 3: 整合和测试**
- [ ] 整合到 Factory Pipeline
- [ ] 端到端测试
- [ ] 性能优化
- [ ] 文档编写

**验收标准**:
- 输入1个60分钟英文视频
- 输出8个中文短视频（抖音/小红书/B站）
- 每个视频包含：中文配音 + 中文字幕 + 片头片尾 + 转场

### P1 - 自研替换（4-6周）

**目标**: 降低OpusClip成本

- [ ] 实现 LLM 高光识别
- [ ] 实现 YOLOv8 主体检测
- [ ] 实现智能裁剪算法
- [ ] A/B测试对比效果

---

## 📊 成本估算

### 方案A: 全部使用 OpusClip

**成本**:
- OpusClip API: $0.20/分钟
- 60分钟视频 = $12
- 每月100个视频 = $1200/月

### 方案B: 混合方案（推荐）

**阶段1（前3个月）**:
- OpusClip API: $1200/月
- 服务器: $100/月
- **总计**: $1300/月

**阶段2（3个月后）**:
- GPU服务器: $300/月（自研AI切片）
- **总计**: $300/月

**节省**: $1000/月（77%成本降低）

---

## ✅ 验收标准

### 功能验收
- [ ] 输入YouTube英文视频URL
- [ ] 自动翻译成中文配音
- [ ] AI识别5-10个高光片段
- [ ] 每个片段智能裁剪成竖屏（9:16）
- [ ] 自动添加片头片尾
- [ ] 烧录中文字幕（平台风格）
- [ ] 添加转场效果
- [ ] 输出3个平台版本

### 质量验收
- [ ] 高光识别准确率 > 80%
- [ ] 智能裁剪主体保留率 > 95%
- [ ] 字幕同步误差 < 0.5秒
- [ ] 转场流畅无卡顿

### 性能验收
- [ ] 60分钟视频处理时间 < 45分钟
- [ ] 支持并发处理3个视频

---

## 🎯 与现有系统的整合

### 整合到 Factory Pipeline

```python
# src/factory/pipeline.py (修改)

class FactoryPipeline:
    async def process(self, task: Task):
        # 现有流程
        if task.scope in ["subtitle_only", "dub_and_copy", "full"]:
            # 1. 长视频加工（已有）
            await self.long_video_processor.process(...)

            # 2. 短视频切片（新增）⭐
            if task.enable_ai_clipping:
                clips = await self.clip_creator.create_clips(
                    video_path=task.dubbed_video,
                    transcript=task.chinese_subtitle,
                    template=task.clip_template
                )

                for clip in clips:
                    # 保存切片产物
                    task.add_product(
                        type="short_clip",
                        path=clip.path,
                        metadata={
                            "title": clip.title,
                            "duration": clip.duration,
                            "platform": clip.platform
                        }
                    )
```

### 新增配置

```yaml
# config/settings.yaml

# AI切片配置
ai_clipping:
  enabled: true
  engine: "opusclip"  # opusclip / llm / hybrid

  # OpusClip配置
  opusclip:
    api_key: "${OPUSCLIP_API_KEY}"
    base_url: "https://api.opus.pro/api"

  # 切片参数
  clip_count: 8
  min_duration: 30
  max_duration: 180

  # 智能裁剪
  auto_reframe:
    enabled: true
    target_aspect: "9:16"
    focus: "auto"  # auto / center / face / screen
```

---

## 下一步

**等待开发者确认**:
1. ✅ 是否采用混合方案（OpusClip + 自研）？
2. ✅ 预算是否可接受（前期$1300/月）？
3. ✅ 3周开发周期是否可接受？
4. ✅ 是否需要调整优先级？

**确认后进入 Step 2（技术设计）**
