# Step 1: Requirements - 自研AI视频切片系统（最终版）

**创建时间**: 2026-03-04 17:30
**需求提出者**: 开发者
**需求澄清者**: Claude
**状态**: 需求确认，准备进入设计

---

## 🎯 核心需求

### 产品定位
**英文长视频 → 中文短视频切片系统（完全自研）**

### 完整流程

```
英文YouTube视频 (60分钟)
    ↓
【阶段1: 翻译配音】(已有)
├─ 下载视频
├─ KlicStudio 翻译成中文
└─ 输出: 中文配音视频 + 中文字幕
    ↓
【阶段2: AI智能切片】(自研)
├─ LLM 分析中文字幕，识别高光片段
├─ 音频特征检测（音量峰值、静音）
├─ 场景变化检测（画面切换）
└─ 综合评分，选出Top 5-10片段
    ↓
【阶段3: 智能裁剪】(自研)
├─ YOLOv8 检测主体（人物/屏幕）
├─ 跟踪主体位置
├─ 智能裁剪成9:16竖屏
└─ 保持主体居中
    ↓
【阶段4: 后处理】(自研)
├─ 片头片尾拼接
├─ 转场效果
├─ 字幕烧录（平台风格）
└─ BGM混音
    ↓
输出: 5-10个中文短视频
```

---

## 🔑 三大核心能力（自研）

### 能力1: AI智能切片

**技术栈**:
- LLM: 语义分析（识别知识点边界）
- PySceneDetect: 场景检测
- librosa: 音频特征提取
- 自研算法: 综合评分

**实现思路**:
```python
# 1. LLM 语义分析
segments = llm_analyze(
    transcript="中文字幕",
    prompt="""
    分析这段视频字幕，识别5-10个知识点片段。
    每个片段：
    - 有明确主题
    - 包含完整观点
    - 30-180秒
    - 有吸引力的开场
    """
)

# 2. 场景检测
from scenedetect import detect, ContentDetector
scenes = detect(video, ContentDetector())

# 3. 音频特征
import librosa
audio, sr = librosa.load(video)
rms = librosa.feature.rms(y=audio)  # 音量
peaks = find_peaks(rms)  # 峰值（掌声/笑声）

# 4. 综合评分
for segment in segments:
    score = (
        segment.semantic_score * 0.6 +  # 语义
        segment.audio_score * 0.2 +     # 音频
        segment.scene_score * 0.2       # 场景
    )
```

### 能力2: 智能裁剪竖屏

**技术栈**:
- YOLOv8: 目标检测（人物/屏幕）
- OpenCV: 图像处理
- 自研算法: 智能跟踪和裁剪

**实现思路**:
```python
from ultralytics import YOLO

# 1. 加载模型
model = YOLO("yolov8n.pt")

# 2. 检测主体
for frame in video:
    results = model(frame)
    persons = results.filter(class="person")
    screens = results.filter(class="tv")

    # 3. 计算焦点区域
    if persons:
        focus = persons[0].bbox  # 人物优先
    elif screens:
        focus = screens[0].bbox  # 屏幕次之
    else:
        focus = center_bbox  # 默认居中

    # 4. 智能裁剪
    cropped = smart_crop(
        frame,
        focus_bbox=focus,
        output_aspect="9:16",
        smooth=True  # 平滑过渡
    )
```

### 能力3: 后处理

**技术栈**:
- FFmpeg: 视频处理
- 自研模板系统

**功能**:
- 片头片尾拼接
- 转场效果（fade/wipe/circle）
- 字幕烧录（平台风格）
- BGM混音

---

## 📐 技术架构

### 新增模块

```
src/creation/
├── __init__.py
├── highlight_detector.py    # 高光识别（LLM+音频+场景）
├── clip_extractor.py        # 片段提取
├── subject_detector.py      # 主体检测（YOLOv8）
├── smart_cropper.py         # 智能裁剪
├── intro_outro.py           # 片头片尾
├── transitions.py           # 转场效果
├── subtitle_renderer.py     # 字幕渲染
├── audio_mixer.py           # 音频混音
└── pipeline.py              # 创作管线
```

---

## 🚀 实施计划

### P0 - MVP（4-5周）

**Week 1: 高光识别**
- [ ] 实现 LLM 语义分析
- [ ] 集成 PySceneDetect
- [ ] 实现音频特征提取（librosa）
- [ ] 实现综合评分算法
- [ ] 测试：识别准确率

**Week 2: 智能裁剪**
- [ ] 集成 YOLOv8
- [ ] 实现主体检测
- [ ] 实现智能裁剪算法
- [ ] 实现平滑跟踪
- [ ] 测试：主体保留率

**Week 3: 后处理**
- [ ] 实现片头片尾拼接
- [ ] 实现转场效果（3种）
- [ ] 实现字幕样式适配
- [ ] 实现 BGM 混音

**Week 4: 整合**
- [ ] 整合到 Factory Pipeline
- [ ] 实现模板系统
- [ ] 端到端测试

**Week 5: 优化**
- [ ] 性能优化（并发处理）
- [ ] 错误处理
- [ ] 文档编写

---

## 💰 成本估算

### 开发成本
- 4-5周开发 × 40小时/周 = 160-200小时

### 运营成本
- GPU服务器: $300-500/月
  - NVIDIA T4: $0.35/小时
  - 每天运行10小时 = $105/月
  - YOLOv8推理 + 视频处理

- 存储: $50/月
  - 临时文件存储

**总计**: $350-550/月

---

## ✅ 验收标准

### 功能验收
- [ ] 输入60分钟英文视频
- [ ] 自动翻译成中文配音
- [ ] AI识别5-10个高光片段
- [ ] 每个片段智能裁剪成9:16
- [ ] 自动添加片头片尾
- [ ] 烧录中文字幕
- [ ] 添加转场效果
- [ ] 输出3个平台版本

### 质量验收
- [ ] 高光识别准确率 > 75%（首版）
- [ ] 主体保留率 > 90%
- [ ] 字幕同步误差 < 0.5秒
- [ ] 转场流畅无卡顿

### 性能验收
- [ ] 60分钟视频处理 < 60分钟
- [ ] 支持并发处理2个视频

---

## 🎯 技术风险和应对

### 风险1: LLM识别不准确
**应对**:
- 优化 prompt 工程
- 结合音频/场景特征
- 提供人工微调接口

### 风险2: YOLOv8检测失败
**应对**:
- 降级到中心裁剪
- 支持手动指定焦点
- 多模型ensemble

### 风险3: 性能瓶颈
**应对**:
- GPU加速（YOLOv8推理）
- 并发处理
- 缓存中间结果

---

## 下一步

**已确认**:
- ✅ 完全自研，不使用OpusClip API
- ✅ 4-5周开发周期
- ✅ $350-550/月运营成本

**进入 Step 2（技术设计）**
