# VideoFactory

自动化视频翻译、配音、二次创作和多平台分发系统

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109-green.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 🎯 核心功能

- **翻译配音**: YouTube视频 → 多引擎ASR → 翻译 → TTS配音
- **智能切片**: AI识别高光片段，自动生成短视频
- **二次创作**: 长视频处理、短切片、封面、元数据生成
- **多平台发布**: 抖音、B站、小红书、YouTube自动分发
- **工程化流程**: 完整的需求→设计→实现→验证→发布流程

---

## 🚀 快速开始

### 1. 克隆仓库
```bash
git clone https://github.com/YOUR_USERNAME/video-factory.git
cd video-factory
```

### 2. 安装依赖
```bash
python3.11 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

### 3. 配置
```bash
# 复制配置模板
cp config/settings.example.yaml config/settings.yaml

# 编辑配置文件，填入你的API密钥
vim config/settings.yaml
```

可选本机运行配置：
- `VF_PYTHON_BIN`：显式指定 Python 3.11 解释器
- `VF_FFMPEG_PATH`：指定 ffmpeg 路径
- 也可以在 `config/settings.yaml` 中填写 `ffmpeg.path`、`ffprobe_path`

### 4. 启动服务
```bash
bash scripts/start_all.sh
```

### 5. 访问界面
打开浏览器访问: http://127.0.0.1:9000

---

## 📋 架构

```
video-factory/
├── src/                    # 核心业务逻辑
│   ├── core/              # 任务状态机、存储、配置
│   ├── asr/               # ASR路由（YouTube/Whisper/火山引擎）
│   ├── tts/               # TTS路由（火山引擎）
│   ├── production/        # 自管翻译配音流程
│   ├── factory/           # 二次创作（切片、封面、元数据）
│   └── distribute/        # 多平台发布调度
├── api/                   # FastAPI服务
├── web/                   # Web界面
├── workers/               # 后台任务编排
├── tests/                 # 测试套件（30+用例）
└── workflow/              # 工程化流程文档
```

---

## 🛠️ 技术栈

- **后端**: FastAPI + asyncio
- **前端**: Jinja2 + HTMX + Alpine.js
- **视频处理**: FFmpeg
- **AI**: OpenAI API / Groq / 火山引擎
- **存储**: Cloudflare R2 + 本地
- **测试**: pytest + Playwright

---

## 📚 文档

- [协作规范](workflow/COLLABORATION_GUIDE.md) - Claude + Codex 协作流程
- [快速上手](workflow/QUICKSTART.md) - 5分钟快速入门
- [架构文档](workflow/architecture.md) - 系统架构说明
- [测试手册](workflow/testing-playbook.md) - 测试流程
- [GitHub工作流](workflow/GITHUB_SETUP.md) - Git使用规范

---

## 🤝 协作开发

本项目采用 [vibe-coding-cn](https://github.com/tukuaiai/vibe-coding-cn) 工程化方法论：

1. **需求澄清** (Step 1) - Claude主导
2. **方案设计** (Step 2) - Claude主导
3. **代码实现** (Step 3) - Codex主导
4. **测试验证** (Step 4) - Claude审查
5. **发布复盘** (Step 5) - Claude总结

详见 [AGENTS.md](AGENTS.md) 和 [workflow/COLLABORATION_GUIDE.md](workflow/COLLABORATION_GUIDE.md)

---

## 🧪 测试

```bash
# 运行所有测试
./.venv/bin/python -m pytest -q

# 运行特定测试
./.venv/bin/python -m pytest -q tests/test_asr_router.py

# 查看覆盖率
./.venv/bin/python -m pytest --cov=src
```

---

## 📦 部署

```bash
# 生产环境启动
bash scripts/start_all.sh restart

# 检查服务状态
bash scripts/start_all.sh status

# 停止服务
bash scripts/start_all.sh stop
```

---

## 🔒 安全

- ⚠️ **不要**提交 `config/settings.yaml` 到 Git
- ⚠️ **不要**提交包含API密钥的文件
- ✅ 使用 `config/settings.local.yaml` 存储本地配置
- ✅ 使用环境变量存储敏感信息

---

## 📄 License

MIT License - 详见 [LICENSE](LICENSE)

---

## 🙏 致谢

- [social-auto-upload](https://github.com/dreammis/social-auto-upload) - 多平台发布
- [vibe-coding-cn](https://github.com/tukuaiai/vibe-coding-cn) - 工程化方法论
