# Step 2: 技术设计 - AI视频切片系统（自研方案）

**创建时间**: 2026-03-04 17:35
**设计者**: Claude
**状态**: 设计中

---

## 🎯 设计目标

基于 Step 1 的需求，设计完全自研的 AI 视频切片系统：
1. **AI 智能切片**: LLM + PySceneDetect + librosa
2. **智能竖屏裁剪**: YOLOv8 + OpenCV
3. **自动化后处理**: FFmpeg + 模板系统

---

## 🏗️ 系统架构

```
Factory Pipeline (编排层)
    ↓
Translation Layer (已有: KlicStudio)
    ↓
Clipping Layer (新增)
├─ HighlightDetector (LLM + PySceneDetect + librosa)
└─ ClipExtractor
    ↓
Cropping Layer (新增)
├─ SubjectDetector (YOLOv8)
├─ SubjectTracker
└─ SmartCropper (OpenCV)
    ↓
Composition Layer (新增)
├─ IntroOutroComposer
├─ SubtitleRenderer
├─ TransitionComposer
└─ AudioMixer
    ↓
输出短视频
```

---

## 📦 新增目录结构

```
src/creation/
├── __init__.py
├── highlight_detector.py
├── clip_extractor.py
├── subject_detector.py
├── smart_cropper.py
├── video_composer.py
└── pipeline.py
```

---

## 🔍 模块 1: HighlightDetector (高光识别)

### 职责
分析视频内容，识别5-10个高光片段

### 技术方案

**三路并行分析**:
1. **LLM 语义分析** (权重 60%)
   - 输入: 中文字幕 SRT
   - 任务: 识别话题边界、关键观点
   - 模型: OpenAI GPT-4 / Claude
   
2. **场景检测** (权重 20%)
   - 工具: PySceneDetect
   - 检测: 画面切换、PPT翻页
   
3. **音频特征** (权重 20%)
   - 工具: librosa
   - 检测: 音量峰值、掌声、笑声

### 核心接口

```python
class HighlightDetector:
    async def detect(
        self,
        video_path: str,
        subtitle_path: str,
        clip_count: int = 8,
        min_duration: int = 30,
        max_duration: int = 180
    ) -> List[Segment]:
        """
        返回:
        [
            {
                "start": 0.0,
                "end": 125.5,
                "title": "什么是Docker",
                "score": 0.92,
                "keywords": ["Docker", "容器"]
            },
            ...
        ]
        """
```

### 算法流程

```
1. LLM 分析字幕 → 候选片段 (语义边界)
2. PySceneDetect → 场景变化时间点
3. librosa → 音频峰值时间点
4. 综合评分 = 0.6*语义 + 0.2*场景 + 0.2*音频
5. 排序取 Top N
6. 调整边界 (对齐场景/音频边界)
```


---

## 🎨 模块 2: SmartCropper (智能裁剪)

### 职责
将横屏视频 (16:9) 智能裁剪为竖屏 (9:16)，保持主体居中

### 技术方案

**两阶段处理**:
1. **主体检测** (YOLOv8)
   - 检测: 人物、屏幕、PPT区域
   - 输出: 每帧的主体边界框
   
2. **智能裁剪** (OpenCV)
   - 跟踪主体位置
   - 动态调整裁剪框
   - 平滑过渡 (避免抖动)

### 核心接口

```python
class SmartCropper:
    async def crop(
        self,
        video_path: str,
        output_aspect: str = "9:16",
        focus: str = "auto"  # auto/center/face
    ) -> str:
        """返回裁剪后的视频路径"""
```

### 算法流程

```
1. YOLOv8 检测每帧主体 → 边界框序列
2. 计算裁剪框中心点 (主体中心)
3. 应用卡尔曼滤波 (平滑轨迹)
4. FFmpeg 裁剪 + 缩放
```

### 性能优化
- 每5帧检测一次 (降低计算量)
- 批量处理 (GPU加速)


---

## 🎬 模块 3: VideoComposer (视频合成)

### 职责
组装最终短视频：片头 + 正文 + 片尾 + 字幕 + BGM + 转场

### 核心接口

```python
class VideoComposer:
    async def compose(
        self,
        content_video: str,
        subtitle_path: str,
        template: dict
    ) -> str:
        """返回最终视频路径"""
```

### 处理流程

```
1. 加载模板配置
2. 拼接: 片头 + 正文 + 片尾
3. 添加转场效果 (FFmpeg xfade)
4. 烧录字幕 (FFmpeg subtitles filter)
5. 混音 BGM (FFmpeg amix)
```

### FFmpeg 命令示例

```bash
# 拼接 + 转场
ffmpeg -i intro.mp4 -i content.mp4 -i outro.mp4 \
  -filter_complex "[0][1]xfade=transition=fade:duration=0.5[v1];[v1][2]xfade=transition=fade:duration=0.5[v]" \
  -map "[v]" output.mp4

# 字幕 + BGM
ffmpeg -i video.mp4 -i bgm.mp3 -vf "subtitles=sub.srt:force_style='FontSize=24'" \
  -filter_complex "[1]volume=0.3[a1];[0:a][a1]amix=inputs=2[a]" \
  -map 0:v -map "[a]" final.mp4
```


---

## 📊 数据流设计

### 端到端数据流

```
输入: YouTube URL
  ↓
[已有] 下载 + 翻译配音
  ↓
中文配音视频 + 中文字幕.srt
  ↓
[新增] HighlightDetector.detect()
  ↓
Segment[] (5-10个片段元数据)
  ↓
[新增] ClipExtractor.extract()
  ↓
clip_001.mp4, clip_002.mp4, ...
  ↓
[新增] SmartCropper.crop()
  ↓
clip_001_9x16.mp4, ...
  ↓
[新增] VideoComposer.compose()
  ↓
final_001.mp4 (带片头/片尾/字幕/BGM)
```

### 关键数据结构

```python
# Segment (片段元数据)
{
    "id": "seg_001",
    "start": 0.0,
    "end": 125.5,
    "title": "什么是Docker",
    "score": 0.92,
    "keywords": ["Docker", "容器"],
    "subtitle_text": "..."
}

# Template (模板配置)
{
    "name": "douyin_educational",
    "platform": "douyin",
    "aspect_ratio": "9:16",
    "intro": {"path": "assets/intro.mp4", "duration": 3},
    "outro": {"path": "assets/outro.mp4", "duration": 5},
    "subtitle_style": {...},
    "bgm": {"category": "educational", "volume": 0.25},
    "transitions": {"type": "fade", "duration": 0.5}
}
```


---

## 🛠️ 技术栈

### Python 依赖

```
# AI/ML
openai>=1.0.0           # LLM API
anthropic>=0.18.0       # Claude API (备选)
ultralytics>=8.0.0      # YOLOv8
opencv-python>=4.8.0    # 图像处理

# 视频分析
scenedetect>=0.6.0      # 场景检测
librosa>=0.10.0         # 音频分析
ffmpeg-python>=0.2.0    # FFmpeg封装

# 字幕处理
pysrt>=1.1.0            # SRT解析
```

### 系统依赖

```bash
# FFmpeg (必需)
brew install ffmpeg

# YOLOv8 模型 (首次运行自动下载)
yolov8n.pt (~6MB)
```


---

## 📋 模块影响清单

### 新增文件 (9个)

```
src/creation/
├── __init__.py
├── highlight_detector.py    # ~200 行
├── clip_extractor.py        # ~100 行
├── subject_detector.py      # ~150 行
├── smart_cropper.py         # ~200 行
├── video_composer.py        # ~250 行
└── pipeline.py              # ~150 行

config/
└── templates/
    └── douyin_educational.yaml  # 模板配置
```

### 修改文件 (2个)

```
src/factory/pipeline.py
- 新增: 调用 creation.pipeline

requirements.txt
- 新增: 7个依赖包
```

### 无影响文件
- 现有翻译、下载、分发模块完全不变
- 向后兼容：不启用 AI 切片时，系统行为不变


---

## ⚙️ 配置变更

### config/settings.yaml (新增)

```yaml
ai_clipping:
  enabled: false  # 默认关闭，向后兼容
  
  # LLM 配置
  llm:
    provider: "openai"  # openai / anthropic
    model: "gpt-4"
    api_key: "${OPENAI_API_KEY}"
  
  # 切片参数
  clip_count: 8
  min_duration: 30
  max_duration: 180
  
  # 智能裁剪
  cropping:
    enabled: true
    target_aspect: "9:16"
    model: "yolov8n.pt"
  
  # 模板
  default_template: "douyin_educational"
```


---

## 🚨 错误处理

### 降级策略

```python
# 1. LLM 失败 → 降级为固定时长切分
if llm_analysis_failed:
    segments = fixed_duration_split(video, duration=120)

# 2. YOLOv8 检测失败 → 降级为中心裁剪
if subject_detection_failed:
    crop_mode = "center"

# 3. 场景检测失败 → 仅使用 LLM 结果
if scene_detect_failed:
    use_llm_only = True
```

### 日志记录

```python
# 关键节点日志
logger.info(f"HighlightDetector: 识别到 {len(segments)} 个片段")
logger.info(f"SmartCropper: 检测到主体 {subject_type}")
logger.warning(f"LLM 调用失败，降级为固定切分")
```


---

## 🔄 回滚方案

### 回滚步骤

```bash
# 1. 关闭 AI 切片功能
config/settings.yaml: ai_clipping.enabled = false

# 2. 删除新增模块 (如需完全回滚)
rm -rf src/creation/

# 3. 恢复依赖
git checkout requirements.txt
```

### 回滚影响
- 零影响：现有功能完全不受影响
- 数据安全：不涉及数据库变更


---

## 📅 实施计划

### Week 1: 基础模块
- [ ] HighlightDetector (LLM + PySceneDetect)
- [ ] ClipExtractor
- [ ] 单元测试

### Week 2: 智能裁剪
- [ ] SubjectDetector (YOLOv8)
- [ ] SmartCropper
- [ ] 单元测试

### Week 3: 视频合成
- [ ] VideoComposer
- [ ] 模板系统
- [ ] 单元测试

### Week 4: 集成测试
- [ ] Pipeline 集成
- [ ] 端到端测试
- [ ] 性能优化

---

## ✅ 完成定义

### 设计完成标准
- [x] 影响文件可枚举 (11个文件)
- [x] 核心接口已定义
- [x] 数据流已明确
- [x] 错误处理策略已制定
- [x] 回滚方案已确定
- [x] 方案可直接进入实现

### 下一步
进入 **Step 3: 实施** 阶段

