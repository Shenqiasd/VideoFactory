# GitHub 上传和 Git 工作流计划

**创建时间**: 2026-03-06
**目标**: 将 VideoFactory 项目上传到 GitHub，建立标准 Git 工作流

---

## 📋 Phase 1: 准备工作（10分钟）

### 1.1 检查敏感信息
```bash
# 检查是否有 API Key 泄露
grep -r "api_key" config/ --include="*.yaml" | grep -v "example"
grep -r "token" config/ --include="*.yaml" | grep -v "example"
grep -r "gsk_" . --exclude-dir={.git,node_modules,venv}
```

### 1.2 备份敏感配置
```bash
# 备份当前配置
cp config/settings.yaml config/settings.local.yaml
```

### 1.3 验证 .gitignore
```bash
# 确认敏感文件被忽略
git status --ignored
```

---

## 📋 Phase 2: 初始化 Git（5分钟）

### 2.1 初始化仓库
```bash
cd /Users/enesource/Projects/video-factory
git init
git branch -M main
```

### 2.2 首次提交
```bash
git add .
git commit -m "Initial commit: VideoFactory v0.1.0

- 完整的翻译配音流程（YouTube → KlicStudio → 质检）
- 二次创作能力（长视频、短切片、封面、元数据）
- 多平台发布调度器
- Web 管理界面
- 30+ 测试用例
- 工程化 workflow 流程

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## 📋 Phase 3: 创建 GitHub 仓库（5分钟）

### 3.1 在 GitHub 创建仓库
1. 访问 https://github.com/new
2. 仓库名: `video-factory`
3. 描述: `自动化视频翻译、配音、二次创作和多平台分发系统`
4. 可见性: **Private**（推荐）或 Public
5. **不要**勾选 "Add README" / ".gitignore" / "license"

### 3.2 关联远程仓库
```bash
git remote add origin git@github.com:YOUR_USERNAME/video-factory.git
git push -u origin main
```

---

## 📋 Phase 4: Git 工作流规范

### 4.1 分支策略

**主分支**:
- `main` - 生产环境，稳定版本
- `develop` - 开发主线，集成分支

**功能分支**:
- `feature/xxx` - 新功能开发
- `fix/xxx` - Bug 修复
- `refactor/xxx` - 代码重构

### 4.2 工作流程

**开发新功能**:
```bash
# 1. 从 develop 创建功能分支
git checkout develop
git pull origin develop
git checkout -b feature/volcengine-integration

# 2. 开发 + 提交
git add src/asr/volcengine_asr.py
git commit -m "[step3] Add Volcengine ASR integration

- Implement WebSocket streaming
- Add fallback to HTTP API
- Update tests

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"

# 3. 推送到远程
git push origin feature/volcengine-integration

# 4. 创建 Pull Request (在 GitHub 网页操作)
# 5. Code Review 通过后合并到 develop
```

**发布版本**:
```bash
# 1. 从 develop 创建 release 分支
git checkout -b release/v0.2.0 develop

# 2. 更新版本号
# 编辑 pyproject.toml: version = "0.2.0"

# 3. 提交并合并到 main
git commit -m "Release v0.2.0"
git checkout main
git merge --no-ff release/v0.2.0
git tag -a v0.2.0 -m "Release v0.2.0"
git push origin main --tags

# 4. 合并回 develop
git checkout develop
git merge --no-ff release/v0.2.0
git push origin develop
```

---

## 📋 Phase 5: 提交规范

### 5.1 Commit Message 格式
```
[stepX] <type>: <subject>

<body>

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
```

**Type 类型**:
- `feat`: 新功能
- `fix`: Bug 修复
- `refactor`: 重构
- `test`: 测试
- `docs`: 文档
- `chore`: 构建/工具

**示例**:
```bash
git commit -m "[step3] feat: Add YouTube subtitle fetching

- Implement YouTubeSubtitleASR provider
- Add fallback from manual to auto-generated
- Update ASR router priority

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## 📋 Phase 6: 协作规范

### 6.1 Claude + Codex 协作流程

**Claude (设计 + 审查)**:
```bash
# Step 1-2: 需求 + 设计
git checkout develop
git checkout -b feature/xxx
# 创建设计文档
git add workflow/steps/
git commit -m "[step2] docs: Design for XXX feature"
git push origin feature/xxx
```

**Codex (实现)**:
```bash
# Step 3: 实现
git checkout feature/xxx
git pull origin feature/xxx
# 实现代码
git add src/
git commit -m "[step3] feat: Implement XXX"
git push origin feature/xxx
```

**Claude (审查)**:
```bash
# Step 4: 审查
git checkout feature/xxx
git pull origin feature/xxx
# 审查代码，提出修改建议
```

**开发者 (验收 + 合并)**:
```bash
# Step 5: 验收
pytest
# 通过后合并
git checkout develop
git merge --no-ff feature/xxx
git push origin develop
```

---

## 📋 Phase 7: GitHub 配置

### 7.1 保护分支规则

在 GitHub 仓库设置中配置:

**main 分支**:
- ✅ Require pull request reviews (1 reviewer)
- ✅ Require status checks to pass (pytest)
- ✅ Require branches to be up to date
- ✅ Do not allow bypassing

**develop 分支**:
- ✅ Require status checks to pass (pytest)

### 7.2 GitHub Actions (可选)

创建 `.github/workflows/test.yml`:
```yaml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: pytest
```

---

## 📋 Phase 8: 日常使用

### 8.1 每日工作流
```bash
# 早上：同步最新代码
git checkout develop
git pull origin develop

# 开发：创建功能分支
git checkout -b feature/new-feature

# 提交：遵循规范
git add .
git commit -m "[step3] feat: XXX"

# 推送：提交到远程
git push origin feature/new-feature

# 合并：通过 PR 合并到 develop
```

### 8.2 常用命令
```bash
# 查看状态
git status

# 查看历史
git log --oneline --graph

# 撤销修改
git checkout -- <file>

# 暂存工作
git stash
git stash pop

# 查看差异
git diff
```

---

## ✅ 检查清单

上传前确认:
- [ ] `.gitignore` 已创建
- [ ] `config/settings.yaml` 已备份为 `.local.yaml`
- [ ] `config/settings.example.yaml` 已创建
- [ ] 敏感信息已移除（API Key、Token）
- [ ] README.md 已更新
- [ ] 测试通过 (`pytest`)

上传后确认:
- [ ] GitHub 仓库已创建
- [ ] 远程仓库已关联
- [ ] 首次提交已推送
- [ ] 分支保护规则已配置
- [ ] 协作者已邀请

---

## 🚀 快速开始

```bash
# 一键执行 Phase 1-3
cd /Users/enesource/Projects/video-factory

# 备份配置
cp config/settings.yaml config/settings.local.yaml

# 初始化 Git
git init
git branch -M main
git add .
git commit -m "Initial commit: VideoFactory v0.1.0"

# 关联 GitHub（替换 YOUR_USERNAME）
git remote add origin git@github.com:YOUR_USERNAME/video-factory.git
git push -u origin main

# 创建 develop 分支
git checkout -b develop
git push -u origin develop
```

完成！🎉
