# VideoFactory

自动化视频翻译、配音、二次创作与多平台分发系统。

[![CI](https://github.com/Shenqiasd/VideoFactory/actions/workflows/ci.yml/badge.svg)](https://github.com/Shenqiasd/VideoFactory/actions)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109-green.svg)](https://fastapi.tiangolo.com/)

## 功能概览

| 模块 | 说明 |
|------|------|
| **视频翻译配音** | YouTube 视频下载 → 多引擎 ASR（YouTube/Whisper/火山引擎）→ LLM 翻译 → TTS 配音 → 字幕合成 |
| **二次创作** | AI 高光片段检测、智能裁剪、短视频切片、封面生成、元数据生成 |
| **多平台发布** | 统一 OAuth2 认证，支持 10 个平台：YouTube、Bilibili、TikTok、抖音、Facebook、Instagram、Twitter、Pinterest、LinkedIn、快手 |
| **发布队列** | 异步任务队列（3 Worker）、定时发布、重试机制、批量发布 |
| **发布模板** | 保存常用发布配置，一键全平台发布 |
| **数据分析** | 跨平台内容统计仪表盘，Token 健康监控 |
| **频道监控** | YouTube 频道 RSS 轮询，新视频自动创建任务 |
| **用户认证** | 用户名/密码登录，bcrypt 加密，Session Cookie |
| **API 限流** | 基于 slowapi 的请求频率限制，保护 API 额度 |

## 快速开始

### 本地开发

```bash
# 克隆仓库
git clone https://github.com/Shenqiasd/VideoFactory.git
cd VideoFactory

# 安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 配置
cp config/settings.example.yaml config/settings.yaml
# 编辑 config/settings.yaml，填入 API 密钥

# 启动
python -m uvicorn api.server:app --host 0.0.0.0 --port 9000

# 访问 http://localhost:9000
```

### Docker 部署

```bash
# 使用 Docker Compose（含 PostgreSQL）
cp .env.example .env
# 编辑 .env 填入配置
docker compose up -d
```

### Railway 一键部署

项目内置 `railway.toml` 和 `Dockerfile`，支持 [Railway](https://railway.app) 一键部署：

1. 在 Railway 创建项目，连接 GitHub 仓库
2. 设置环境变量（或挂载 `config/settings.yaml`）
3. 部署后访问分配的域名

## 项目结构

```
VideoFactory/
├── api/                        # FastAPI 服务
│   ├── server.py               # 应用入口、生命周期管理
│   ├── auth.py                 # 用户认证（登录/注册/Session）
│   ├── rate_limit.py           # API 限流中间件
│   └── routes/                 # API 路由
│       ├── pages.py            # 前端页面路由
│       ├── publish_v2.py       # 多平台发布 API（含批量发布）
│       ├── oauth.py            # OAuth2 回调处理
│       ├── analytics.py        # 数据分析 API
│       ├── templates.py        # 发布模板 CRUD API
│       ├── monitor.py          # 频道监控 API
│       ├── system.py           # 系统设置 API
│       ├── production.py       # 翻译配音流水线 API
│       ├── factory.py          # 二次创作 API
│       ├── distribute.py       # 发布调度 API
│       ├── storage.py          # 存储管理 API
│       └── tasks.py            # 任务管理 API
├── src/                        # 核心业务逻辑
│   ├── core/                   # 基础设施
│   │   ├── database.py         # SQLite 数据库（线程安全 CRUD）
│   │   ├── database_async.py   # PostgreSQL 异步数据库
│   │   ├── config.py           # 配置管理
│   │   ├── task.py             # 任务状态机
│   │   └── storage.py          # 存储管理（本地 + R2）
│   ├── platform_services/      # 多平台服务层
│   │   ├── base.py             # PlatformService 抽象基类
│   │   ├── registry.py         # 平台服务注册中心
│   │   ├── token_manager.py    # OAuth Token 管理（TTLCache + DB）
│   │   ├── publish_queue.py    # 异步发布队列（3 Worker + 调度器）
│   │   ├── analytics.py        # 跨平台数据分析服务
│   │   ├── templates.py        # 发布模板服务
│   │   ├── youtube.py          # YouTube 平台实现
│   │   ├── bilibili.py         # Bilibili 平台实现
│   │   ├── tiktok.py           # TikTok 平台实现
│   │   ├── douyin.py           # 抖音平台实现
│   │   ├── facebook.py         # Facebook 平台实现
│   │   ├── instagram.py        # Instagram 平台实现
│   │   ├── twitter.py          # Twitter/X 平台实现
│   │   ├── pinterest.py        # Pinterest 平台实现
│   │   └── linkedin.py         # LinkedIn 平台实现
│   ├── asr/                    # 语音识别（ASR 路由）
│   ├── tts/                    # 语音合成（火山引擎 TTS）
│   ├── translation/            # 翻译（LLM / 火山方舟）
│   ├── production/             # 翻译配音流水线
│   ├── creation/               # 二次创作（切片/封面/字幕）
│   ├── factory/                # 加工流水线
│   ├── source/                 # 视频源（下载/频道监控）
│   └── distribute/             # 旧版发布调度
├── web/templates/              # Jinja2 + Tailwind CSS + Alpine.js 前端
├── workers/                    # 后台任务编排器
├── tests/                      # 测试套件（560+ 用例）
├── config/                     # 配置文件
│   └── settings.example.yaml   # 配置模板
├── scripts/                    # 工具脚本
├── alembic/                    # 数据库迁移（PostgreSQL）
├── Dockerfile                  # Docker 构建
├── docker-compose.yml          # Docker Compose 编排
└── railway.toml                # Railway 部署配置
```

## 技术栈

- **后端**: Python 3.11+ / FastAPI / asyncio / Uvicorn
- **前端**: Jinja2 / Tailwind CSS / Alpine.js / Lucide Icons
- **数据库**: SQLite（开发）/ PostgreSQL（生产，通过 asyncpg）
- **视频处理**: FFmpeg / yt-dlp
- **AI 服务**: OpenAI API / Groq / 火山引擎（ASR + TTS + 翻译）
- **对象存储**: Cloudflare R2 / 本地存储
- **认证**: bcrypt + Session Cookie / OAuth2（多平台）
- **测试**: pytest（560+ 用例）/ Playwright（E2E）
- **部署**: Docker / Docker Compose / Railway

## 配置说明

所有配置通过 `config/settings.yaml` 管理（从 `settings.example.yaml` 复制）：

| 配置项 | 说明 |
|--------|------|
| `llm` | LLM API（Groq / OpenAI 兼容）|
| `translation` | 翻译引擎（火山方舟 / 本地 LLM）|
| `asr` | 语音识别（YouTube 字幕 / Whisper / 火山引擎）|
| `tts` | 语音合成（火山引擎 Seed-TTS）|
| `oauth` | 各平台 OAuth2 凭据（client_id / client_secret）|
| `database.url` | 数据库连接（留空 = SQLite，填 PostgreSQL URL = 生产模式）|
| `storage` | 存储配置（R2 / 本地路径）|
| `monitor` | 频道监控开关与频率 |

环境变量：
- `VF_PYTHON_BIN` — 指定 Python 解释器路径
- `VF_FFMPEG_PATH` — 指定 FFmpeg 路径
- `PORT` — 服务监听端口（默认 9000，Railway 自动注入 8080）
- `DATABASE_URL` — PostgreSQL 连接字符串（覆盖 settings.yaml）

## 测试

```bash
# 运行全部单元测试（排除 E2E）
python -m pytest -q --ignore=tests/e2e/ --tb=short

# 运行特定模块测试
python -m pytest tests/test_analytics.py -v
python -m pytest tests/test_publish_templates.py -v

# E2E 测试（需要 Playwright）
python -m pytest tests/e2e/ -v
```

## 数据库表结构

| 表名 | 用途 |
|------|------|
| `accounts` | 用户账户（登录认证）|
| `platform_accounts` | 平台账号（OAuth 绑定状态）|
| `oauth_credentials` | OAuth Token 存储（加密）|
| `publish_tasks_v2` | 发布任务（状态机：pending → publishing → published）|
| `publish_templates` | 发布模板配置 |
| `content_analytics` | 内容数据统计（views/likes/comments/shares）|
| `publish_tasks` | 旧版发布任务 |
| `publish_jobs` / `publish_job_events` | 旧版发布作业 |

## API 端点

主要 API 分组（完整文档访问 `/docs`）：

| 前缀 | 说明 |
|------|------|
| `/api/publish/v2` | 多平台发布（创建/查询/重试/取消/批量）|
| `/api/oauth` | OAuth2 认证（授权 URL / 回调 / 平台列表）|
| `/api/analytics` | 数据分析（汇总/同步/Top 内容/Token 健康）|
| `/api/templates` | 发布模板 CRUD + 应用 |
| `/api/monitor` | 频道监控（CRUD / 手动检查）|
| `/api/system` | 系统设置（读取/保存/运行时信息）|
| `/api/production` | 翻译配音流水线 |
| `/api/factory` | 二次创作流水线 |
| `/api/health` | 健康检查（Worker 状态 + 队列统计）|

## 安全

- 所有 API 端点需登录认证（Bootstrap 模式下首次注册免认证）
- OAuth Token 存储在数据库，通过 Token Manager 自动刷新
- 设置页面敏感字段（API Key / Token）GET 响应自动脱敏
- API 限流保护（创建型端点 10 次/分钟，批量 3 次/分钟）
- `config/settings.yaml`、`.env`、`credentials.json` 等均在 `.gitignore` 中
- 登录重定向做开放重定向防护

## License

MIT
