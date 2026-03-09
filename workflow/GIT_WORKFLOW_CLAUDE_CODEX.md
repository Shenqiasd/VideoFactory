# Claude + Codex Git 工作流

**更新时间**: 2026-03-06

本文档详细说明 Claude 和 Codex 在 Git 协作中的具体操作流程。

---

## 🎯 总体原则

- **Claude**: 负责需求、设计、审查、文档，**不直接修改代码**
- **Codex**: 负责代码实现、测试编写，**不做设计决策**
- **开发者**: 负责需求确认、方案批准、最终验收、合并代码

---

## 📋 完整协作流程

### 阶段 0: 开发者提出需求

**开发者操作**:
```bash
# 1. 确保在最新的 develop 分支
git checkout develop
git pull origin develop

# 2. 告诉 Claude 需求
# "我需要实现火山引擎翻译集成"
```

---

### 阶段 1: Claude - 需求澄清 (Step 1)

**Claude 操作**:
```bash
# 1. 创建功能分支
git checkout develop
git checkout -b feature/volcengine-translation

# 2. 创建需求文档
# 编辑 workflow/steps/step1_requirements_volcengine.md

# 3. 提交需求文档
git add workflow/steps/step1_requirements_volcengine.md
git commit -m "[step1] docs: Add requirements for Volcengine translation

- Define 3 core capabilities
- Specify API endpoints
- Define acceptance criteria

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"

# 4. 推送到远程
git push origin feature/volcengine-translation

# 5. 更新工作流状态
# 编辑 workflow/state/current_step.json
git add workflow/state/current_step.json
git commit -m "[step1] chore: Update workflow state to step1"
git push origin feature/volcengine-translation
```

**输出**:
- `workflow/steps/step1_requirements_volcengine.md`
- `workflow/state/current_step.json` (status: completed)

**等待**: 开发者确认需求

---

### 阶段 2: Claude - 方案设计 (Step 2)

**开发者确认后，Claude 继续**:
```bash
# 1. 切换到功能分支
git checkout feature/volcengine-translation
git pull origin feature/volcengine-translation

# 2. 创建设计文档
# 编辑 workflow/steps/step2_design_volcengine.md

# 3. 提交设计文档
git add workflow/steps/step2_design_volcengine.md
git commit -m "[step2] docs: Design Volcengine translation integration

Architecture:
- TranslationRouter with fallback strategy
- VolcengineTranslator provider
- API endpoints: /api/translate/test

Implementation plan:
- Day 1-2: Core translator
- Day 3: API integration
- Day 4: Testing

Files to modify:
- src/translation/router.py (new)
- src/translation/volcengine.py (new)
- api/routes/translation.py (modify)
- config/settings.yaml (modify)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"

# 4. 推送到远程
git push origin feature/volcengine-translation

# 5. 更新工作流状态
git add workflow/state/current_step.json
git commit -m "[step2] chore: Update workflow state to step2 completed"
git push origin feature/volcengine-translation
```

**输出**:
- `workflow/steps/step2_design_volcengine.md`
- 详细的实现路径
- 文件清单
- 接口设计

**等待**: 开发者批准设计

---

### 阶段 3: Codex - 代码实现 (Step 3)

**开发者批准后，Codex 开始实现**:

```bash
# 1. 切换到功能分支
git checkout feature/volcengine-translation
git pull origin feature/volcengine-translation

# 2. 阅读设计文档
cat workflow/steps/step2_design_volcengine.md

# 3. 创建实施文档
# 编辑 workflow/steps/step3_implementation_volcengine.md
git add workflow/steps/step3_implementation_volcengine.md
git commit -m "[step3] docs: Create implementation checklist"
git push origin feature/volcengine-translation

# 4. 实现代码（按设计文档逐步实现）

# 4.1 创建 TranslationRouter
# 编辑 src/translation/router.py
git add src/translation/router.py
git commit -m "[step3] feat: Add TranslationRouter with fallback

- Implement provider priority: volcengine -> llm
- Add fallback strategy
- Add error handling

Co-Authored-By: Codex (Cursor)"
git push origin feature/volcengine-translation

# 4.2 创建 VolcengineTranslator
# 编辑 src/translation/volcengine.py
git add src/translation/volcengine.py
git commit -m "[step3] feat: Implement VolcengineTranslator

- Use Volcengine Ark API (OpenAI compatible)
- Model: doubao-seed-translation
- Add retry logic

Co-Authored-By: Codex (Cursor)"
git push origin feature/volcengine-translation

# 4.3 更新 API 路由
# 编辑 api/routes/translation.py
git add api/routes/translation.py
git commit -m "[step3] feat: Add translation test endpoint

- POST /api/translate/test
- Support quick testing without task

Co-Authored-By: Codex (Cursor)"
git push origin feature/volcengine-translation

# 4.4 编写测试
# 编辑 tests/test_translation_router.py
git add tests/test_translation_router.py
git commit -m "[step3] test: Add TranslationRouter tests

- Test provider fallback
- Test error handling
- Test Volcengine integration

Co-Authored-By: Codex (Cursor)"
git push origin feature/volcengine-translation

# 5. 运行测试
pytest tests/test_translation_router.py

# 6. 更新工作流状态
git add workflow/state/current_step.json
git commit -m "[step3] chore: Mark implementation completed"
git push origin feature/volcengine-translation
```

**关键原则**:
- ✅ **小步提交**: 每完成一个文件就提交
- ✅ **清晰消息**: 说明做了什么、为什么
- ✅ **及时推送**: 每次提交后立即推送
- ✅ **遵循设计**: 严格按照 step2 的设计实现
- ✅ **测试先行**: 实现后立即编写测试

**输出**:
- `src/translation/router.py`
- `src/translation/volcengine.py`
- `api/routes/translation.py`
- `tests/test_translation_router.py`
- 所有测试通过

**通知**: Claude 进行审查

---

### 阶段 4: Claude - 代码审查 (Step 4)

**Codex 完成后，Claude 审查**:

```bash
# 1. 切换到功能分支
git checkout feature/volcengine-translation
git pull origin feature/volcengine-translation

# 2. 审查代码
# 检查：代码规范、错误处理、测试覆盖、是否遵循设计

# 3. 如果发现问题，创建审查报告
# 编辑 workflow/steps/step4_review_volcengine.md
git add workflow/steps/step4_review_volcengine.md
git commit -m "[step4] docs: Code review findings

Issues found:
- Missing timeout handling in Volcengine API call
- Test coverage for error cases insufficient

Suggestions:
- Add timeout parameter (default 30s)
- Add test for API timeout scenario

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
git push origin feature/volcengine-translation

# 4. 如果需要修改，通知 Codex
# Codex 修复后，Claude 重新审查

# 5. 审查通过后，运行完整测试
pytest

# 6. 创建审查通过报告
git add workflow/steps/step4_review_volcengine.md
git commit -m "[step4] docs: Code review passed

✅ Code quality: Excellent
✅ Test coverage: 95%
✅ Follows design: Yes
✅ Error handling: Complete

Ready for verification.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
git push origin feature/volcengine-translation

# 7. 更新工作流状态
git add workflow/state/current_step.json
git commit -m "[step4] chore: Mark review completed"
git push origin feature/volcengine-translation
```

**输出**:
- `workflow/steps/step4_review_volcengine.md`
- 审查意见或通过确认

**通知**: 开发者进行验收

---

### 阶段 5: Claude - 发布准备 (Step 5)

**开发者验收通过后，Claude 准备发布**:

```bash
# 1. 切换到功能分支
git checkout feature/volcengine-translation
git pull origin feature/volcengine-translation

# 2. 更新架构文档
# 编辑 workflow/architecture.md
# 添加新模块说明
git add workflow/architecture.md
git commit -m "[step5] docs: Update architecture for translation module

Added:
- src/translation/ module
- TranslationRouter with fallback
- Volcengine integration

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
git push origin feature/volcengine-translation

# 3. 更新进度日志
# 编辑 workflow/progress.md
git add workflow/progress.md
git commit -m "[step5] docs: Record Volcengine translation completion

Completed:
- Volcengine Ark API integration
- Translation router with fallback
- Test endpoint for quick validation

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
git push origin feature/volcengine-translation

# 4. 创建发布总结
# 编辑 workflow/steps/step5_release_volcengine.md
git add workflow/steps/step5_release_volcengine.md
git commit -m "[step5] docs: Release summary for Volcengine translation

Summary:
- 4 days development
- 3 new files, 1 modified
- 95% test coverage
- Ready for merge

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
git push origin feature/volcengine-translation

# 5. 重置工作流状态
git add workflow/state/current_step.json
git commit -m "[step5] chore: Reset workflow state to idle"
git push origin feature/volcengine-translation
```

**输出**:
- `workflow/architecture.md` (已更新)
- `workflow/progress.md` (已更新)
- `workflow/steps/step5_release_volcengine.md`

**通知**: 开发者合并代码

---

### 阶段 6: 开发者 - 合并代码

**开发者最终操作**:

```bash
# 1. 在 GitHub 创建 Pull Request
# 从 feature/volcengine-translation -> develop

# 2. 检查 PR 内容
# - 所有测试通过
# - 代码审查通过
# - 文档已更新

# 3. 合并到 develop
git checkout develop
git pull origin develop
git merge --no-ff feature/volcengine-translation
git push origin develop

# 4. 删除功能分支（可选）
git branch -d feature/volcengine-translation
git push origin --delete feature/volcengine-translation

# 5. 如果需要发布到生产
git checkout main
git merge --no-ff develop
git tag -a v0.2.0 -m "Release v0.2.0: Add Volcengine translation"
git push origin main --tags
```

---

## 🔄 日常工作流速查

### Claude 每日流程

```bash
# 早上：同步最新代码
git checkout develop
git pull origin develop

# 收到需求：创建功能分支
git checkout -b feature/xxx

# Step 1: 需求文档
# 编辑 workflow/steps/step1_requirements_xxx.md
git add workflow/steps/
git commit -m "[step1] docs: Add requirements for XXX"
git push origin feature/xxx

# Step 2: 设计文档
# 编辑 workflow/steps/step2_design_xxx.md
git add workflow/steps/
git commit -m "[step2] docs: Design XXX feature"
git push origin feature/xxx

# Step 4: 审查代码
git pull origin feature/xxx
# 审查 Codex 的代码
git add workflow/steps/step4_review_xxx.md
git commit -m "[step4] docs: Code review for XXX"
git push origin feature/xxx

# Step 5: 发布准备
git add workflow/architecture.md workflow/progress.md
git commit -m "[step5] docs: Update docs for XXX"
git push origin feature/xxx
```

### Codex 每日流程

```bash
# 早上：同步最新代码
git checkout develop
git pull origin develop

# 收到设计：切换到功能分支
git checkout feature/xxx
git pull origin feature/xxx

# 阅读设计
cat workflow/steps/step2_design_xxx.md

# Step 3: 实现代码（小步提交）
# 实现第一个文件
git add src/xxx/file1.py
git commit -m "[step3] feat: Implement XXX core logic"
git push origin feature/xxx

# 实现第二个文件
git add src/xxx/file2.py
git commit -m "[step3] feat: Add XXX API endpoint"
git push origin feature/xxx

# 编写测试
git add tests/test_xxx.py
git commit -m "[step3] test: Add tests for XXX"
git push origin feature/xxx

# 运行测试
pytest

# 标记完成
git add workflow/state/current_step.json
git commit -m "[step3] chore: Mark implementation completed"
git push origin feature/xxx
```

---

## ⚠️ 注意事项

### Claude 注意事项

1. **不直接修改代码**: 只创建/修改文档和配置
2. **详细设计**: step2 必须给出清晰的实现路径
3. **认真审查**: step4 检查代码质量、测试覆盖、错误处理
4. **及时推送**: 每个步骤完成后立即推送

### Codex 注意事项

1. **遵循设计**: 严格按照 step2 的设计实现
2. **小步提交**: 每完成一个文件就提交
3. **测试先行**: 实现后立即编写测试
4. **清晰消息**: Commit message 说明做了什么
5. **及时推送**: 每次提交后立即推送

### 开发者注意事项

1. **及时确认**: Step 1 需求确认、Step 2 设计批准
2. **最终验收**: Step 4 审查通过后进行验收
3. **合并代码**: 验收通过后合并到 develop
4. **保护分支**: 设置 main/develop 分支保护规则

---

## 📊 提交消息规范

### 格式
```
[stepX] <type>: <subject>

<body>

Co-Authored-By: <author>
```

### Type 类型
- `feat`: 新功能
- `fix`: Bug 修复
- `refactor`: 重构
- `test`: 测试
- `docs`: 文档
- `chore`: 构建/工具

### 示例

**Claude 提交**:
```bash
git commit -m "[step2] docs: Design Volcengine translation

Architecture:
- TranslationRouter with fallback
- VolcengineTranslator provider

Implementation plan: 4 days

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

**Codex 提交**:
```bash
git commit -m "[step3] feat: Implement VolcengineTranslator

- Use Volcengine Ark API
- Model: doubao-seed-translation
- Add retry logic with exponential backoff

Co-Authored-By: Codex (Cursor)"
```

---

## 🎯 完整示例

见 `workflow/COLLABORATION_GUIDE.md` 中的"完整流程示例"章节。

---

## 📚 相关文档

- [协作规范](COLLABORATION_GUIDE.md) - 完整协作流程
- [GitHub 设置](GITHUB_SETUP.md) - Git 工作流规范
- [快速上手](QUICKSTART.md) - 5分钟快速入门
