# AGENTS.md

本项目采用 `workflow/` 下的 5 步执行流程（参考 [vibe-coding-cn](https://github.com/tukuaiai/vibe-coding-cn)，按本仓库落地）。
目标：在 **不删减功能** 的前提下，稳定推进需求、实现、验证与交付。

---

## 🚀 快速开始

**Claude 和 Codex，请先阅读：**
1. **协作规范**：`workflow/COLLABORATION_GUIDE.md` - Claude + Codex 如何协同工作
2. **快速启动**：`workflow/QUICKSTART.md` - 5分钟上手五步流程

**每次开始工作前，先执行：**
```bash
cat workflow/state/current_step.json  # 查看当前步骤和负责人
```

---

## 0. 基本约束

- 所有改动都要先写计划，再改代码，再跑验证。
- 不允许"只改代码不更新文档状态"。
- 默认先最小可行改动，再迭代优化。
- 不删减现有功能；若必须调整行为，先在计划中声明兼容策略。
- **必须遵守 `workflow/COLLABORATION_GUIDE.md` 定义的协作规则**。

---

## 1. 执行入口

### 核心文档
| 文档 | 用途 | 谁来维护 |
|------|------|----------|
| `workflow/COLLABORATION_GUIDE.md` | Claude + Codex 协作规范 | Claude |
| `workflow/QUICKSTART.md` | 快速启动指南 | Claude |
| `workflow/README.md` | workflow 使用说明 | Claude |
| `workflow/state/current_step.json` | 当前步骤状态 | Claude/Codex |
| `workflow/implementation-plan.md` | 当前迭代计划 | Claude |
| `workflow/progress.md` | 执行日志 | Claude/Codex |
| `workflow/architecture.md` | 架构现状 | Claude |
| `workflow/testing-playbook.md` | 测试手册 | Claude |

### 当前运行上下文
- 当前步骤：`workflow/state/current_step.json`
- 运行上下文（可选）：`workflow/state/run_context.json`

---

## 2. 五步流程（必须顺序执行）

```
Step 1: Requirements (需求澄清)    → Claude 主导，开发者确认
Step 2: Design (方案设计)          → Claude 主导，开发者批准
Step 3: Implementation (实现开发)  → Codex 主导，Claude 审查
Step 4: Verification (验证测试)    → Claude + Codex 联合
Step 5: Release (发布复盘)         → Claude 总结，开发者归档
```

### 每个步骤的输出物
1. **需求澄清**：填写 `workflow/steps/step1_requirements.md`
2. **方案设计**：填写 `workflow/steps/step2_design.md`
3. **实现开发**：填写 `workflow/steps/step3_implementation.md` + 代码
4. **验证测试**：填写 `workflow/steps/step4_verification.md` + 测试证据
5. **发布复盘**：填写 `workflow/steps/step5_release.md` + 更新架构文档

### 每完成一步必须：
- 更新 `workflow/state/current_step.json`
- 追加 `workflow/progress.md`

**详细流程说明**：参见 `workflow/COLLABORATION_GUIDE.md`

---

## 3. 角色分工

| 角色 | 主要职责 | 工作阶段 |
|------|----------|----------|
| **Claude** | 需求分析、方案设计、代码审查、测试验证、文档更新 | Step 1/2/4/5 |
| **Codex (Cursor)** | 代码实现、单元测试、Bug修复 | Step 3 |
| **开发者（人类）** | 需求确认、方案批准、最终验收、发布决策 | 所有步骤的决策节点 |

### Claude 的核心职责
- ✅ 提问澄清需求（Step 1）
- ✅ 探索代码库，设计技术方案（Step 2）
- ✅ 审查 Codex 的代码实现（Step 3）
- ✅ 运行测试，填写验证报告（Step 4）
- ✅ 更新架构文档，编写复盘总结（Step 5）

### Codex 的核心职责
- ✅ 按照设计文档实现代码（Step 3）
- ✅ 编写单元测试（Step 3）
- ✅ 修复测试失败的 Bug（Step 4）
- ✅ 遵循 Claude 的代码审查建议（Step 3）

### 开发者的核心职责
- ✅ 确认需求无误（Step 1）
- ✅ 批准技术方案（Step 2）
- ✅ 验收测试结果（Step 4）
- ✅ 决定是否发布（Step 5）

---

## 4. 代码与测试硬门槛

### 最小必跑（每次改动）
```bash
python3.11 -m pytest -q
```

### 服务联调（改动任务链路或页面时）
```bash
bash scripts/start_all.sh status
curl -sS http://127.0.0.1:9000/api/health
curl -sS http://127.0.0.1:8866/health
curl -sS http://127.0.0.1:8877/health
```

**详细测试命令**：参见 `workflow/testing-playbook.md`

---

## 5. 文档同步要求

当发生以下情况时，**Claude 必须同步** `workflow/architecture.md`：
- 新增/删除模块
- 状态机或任务流变更
- API 路由变更
- 外部依赖或服务端口变更

**文档过时是严重违规行为**

---

## 6. 工件管理

- 每次较大迭代将证据放入 `workflow/artifacts/`（日志片段、截图、报告）。
- 文件命名建议：`YYYYMMDD-<topic>-<type>.md|png|log`

---

## 7. 质量门禁

每个步骤必须满足条件才能进入下一步：

| 步骤 | 门禁条件 | 检查者 |
|------|----------|--------|
| Step 1 → 2 | ✅ 需求明确 + 验收标准可量化 | Claude → 开发者确认 |
| Step 2 → 3 | ✅ 方案可执行 + 影响评估完整 | Claude → 开发者批准 |
| Step 3 → 4 | ✅ 本地测试通过 + Claude审查通过 | Codex → Claude审查 |
| Step 4 → 5 | ✅ pytest全绿 + 验收标准满足 | Claude → 开发者验收 |
| Step 5 → 结束 | ✅ 架构文档已更新 + 代码已合并 | Claude → 开发者归档 |

---

## 8. 异常处理协议

| 情况 | 处理方式 | 负责人 |
|------|----------|--------|
| 需求变更 | 回退到 step1，重新澄清 | Claude |
| 设计缺陷 | 回退到 step2，重新设计 | Claude |
| 测试失败 | 停留在 step4，修复后重新验证 | Codex → Claude验证 |
| Claude/Codex 意见冲突 | 升级给开发者裁定 | 开发者 |

---

## 9. 协作示例

### 场景：添加视频水印功能

```markdown
1. 开发者：提出需求
   "我想给翻译后的视频加上水印"

2. Claude (Step 1)：澄清需求
   - 提问：水印位置、格式、是否可配置？
   - 输出：workflow/steps/step1_requirements.md
   - 状态：等待开发者确认

3. 开发者：确认需求
   "右下角固定，PNG图片，默认都加"

4. Claude (Step 2)：设计方案
   - 探索代码库
   - 编写设计文档：workflow/steps/step2_design.md
   - 提供2个方案，推荐方案A
   - 状态：等待开发者批准

5. 开发者：批准方案
   "方案A通过，Codex开始实现"

6. Codex (Step 3)：实现代码
   - 读取设计文档
   - 修改 src/factory/watermark.py
   - 运行测试：pytest -q
   - 记录进度：workflow/progress.md
   - 状态：完成实现，等待审查

7. Claude：审查代码
   - 检查代码规范、测试覆盖
   - 提出优化建议
   - Codex 根据建议修改
   - 状态：审查通过，进入验证

8. Claude (Step 4)：验证测试
   - 运行所有测试：pytest -q
   - 对照验收标准验证
   - 输出：workflow/steps/step4_verification.md
   - 状态：验证通过，等待发布

9. 开发者：批准发布
   "可以合并"

10. Claude (Step 5)：发布复盘
    - 更新 workflow/architecture.md
    - 追加 workflow/progress.md
    - 编写复盘：workflow/steps/step5_release.md
    - 提交代码
    - 重置状态：current_step.json → idle
```

---

## 10. 强制规则（违反立即回退）

1. ❌ **禁止跳步**：必须按 step1 → step5 顺序完成
2. ❌ **禁止无设计实现**：step3 必须基于 step2 的批准方案
3. ❌ **禁止未测试发布**：step5 必须 step4 验证通过
4. ❌ **禁止不更新文档**：改动模块必须同步 `architecture.md`
5. ❌ **禁止不遵守角色分工**：Claude 不能直接实现代码（Step 3），Codex 不能做设计决策（Step 2）

---

## 11. 参考资料

- **完整协作规范**：`workflow/COLLABORATION_GUIDE.md` (必读)
- **快速启动指南**：`workflow/QUICKSTART.md` (推荐)
- **Workflow 使用说明**：`workflow/README.md`
- **Vibe Coding 原文**：https://github.com/tukuaiai/vibe-coding-cn

---

**最后更新**：2026-03-04
**维护者**：video-factory 团队
