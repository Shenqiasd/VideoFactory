# Claude + Codex 协同开发规范

本文档定义 Claude 和 Codex（Cursor）如何在 video-factory 项目中协同工作，严格遵循 [vibe-coding-cn](https://github.com/tukuaiai/vibe-coding-cn) 的工程化理念。

---

## 📐 核心理念

### Vibe Coding 三原则
1. **规划驱动**：先规划后编码，避免返工
2. **AI 为主**：将决策权交给 AI，人类负责审查和把控
3. **上下文索引化**：通过文档和状态文件管理上下文，克服模型窗口限制

### 协同分工哲学
| 角色 | 擅长领域 | 主要职责 |
|------|----------|----------|
| **Claude** | 架构设计、需求分析、文档编写、测试设计 | 全局把控、规划制定、代码审查 |
| **Codex (Cursor)** | 代码实现、快速迭代、细节补全 | 执行实现、单元调试、文件编辑 |
| **开发者（人类）** | 需求澄清、决策裁定、验证把关 | 提需求、做决策、最终验收 |

---

## 🔄 标准五步流程

### 总览
```
Step 1: Requirements (需求澄清)    → Claude 主导，人类确认
Step 2: Design (方案设计)          → Claude 主导，人类批准
Step 3: Implementation (实现开发)  → Codex 主导，Claude 审查
Step 4: Verification (验证测试)    → Claude + Codex 联合
Step 5: Release (发布复盘)         → Claude 总结，人类归档
```

---

## 📋 Step 1: Requirements（需求澄清）

### 🎯 目标
- 将用户模糊需求转化为明确的开发任务
- 识别需求边界、风险和依赖

### 👤 角色分工
| 角色 | 任务 |
|------|------|
| **开发者** | 提出需求或问题描述 |
| **Claude** | 询问澄清性问题、分析影响范围、梳理前置条件 |
| **Codex** | 无参与（等待需求明确） |

### 📝 输出物
1. **文件：** `workflow/steps/step1_requirements.md`
2. **内容：**
   ```markdown
   # Step 1: Requirements - [功能名称]

   ## 需求描述
   [用户原始需求]

   ## 需求澄清
   - Q: [Claude提出的问题]
   - A: [开发者回答]

   ## 功能范围
   - 包含：[明确要做什么]
   - 不包含：[明确不做什么]

   ## 前置条件
   - 依赖的模块/服务
   - 需要的配置/数据

   ## 风险识别
   - 技术风险
   - 兼容性风险
   - 性能风险

   ## 验收标准
   - [ ] 标准1
   - [ ] 标准2
   ```

### ✅ 检查门槛
- [ ] 需求明确，无二义性
- [ ] 验收标准可量化
- [ ] 开发者确认需求无误

### 🔄 状态更新
```bash
# Claude 执行
cat > workflow/state/current_step.json <<EOF
{
  "step": "step1_requirements",
  "status": "in_progress",
  "task": "[功能名称]",
  "owner": "claude",
  "updated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
```

---

## 🎨 Step 2: Design（方案设计）

### 🎯 目标
- 设计技术方案，选择实现路径
- 识别需要修改的文件和模块

### 👤 角色分工
| 角色 | 任务 |
|------|------|
| **Claude** | 探索代码库、设计架构、编写伪代码、提出选项 |
| **开发者** | 审查方案、选择方案、批准设计 |
| **Codex** | 无参与（等待设计批准） |

### 📝 输出物
1. **文件：** `workflow/steps/step2_design.md`
2. **内容：**
   ```markdown
   # Step 2: Design - [功能名称]

   ## 技术方案
   ### 方案选择
   - **方案A**：[描述] - [优缺点]
   - **方案B**：[描述] - [优缺点]
   - **推荐**：方案A（原因）

   ### 实现路径
   1. 修改 `src/xxx.py`：[做什么]
   2. 新增 `src/yyy.py`：[做什么]
   3. 更新 `tests/test_zzz.py`：[做什么]

   ## 关键代码骨架
   ```python
   # 伪代码示例
   class NewFeature:
       def process(self):
           # 步骤1
           # 步骤2
   ```

   ## 影响分析
   - 修改的文件：[列表]
   - 影响的模块：[列表]
   - 需要更新的测试：[列表]
   - 需要更新的文档：[列表]

   ## 回退策略
   - 如果失败，如何回滚

   ## 时间估算
   - 开发：[X小时]
   - 测试：[Y小时]
   ```

### ✅ 检查门槛
- [ ] 方案明确可执行
- [ ] 影响范围已评估
- [ ] 开发者批准方案

### 🔄 状态更新
```bash
# Claude 执行（待批准时）
jq '.status = "pending_approval" | .owner = "human"' \
   workflow/state/current_step.json > tmp && mv tmp workflow/state/current_step.json

# 开发者批准后，Claude 执行
jq '.step = "step2_design" | .status = "approved" | .owner = "codex"' \
   workflow/state/current_step.json > tmp && mv tmp workflow/state/current_step.json
```

---

## ⚙️ Step 3: Implementation（实现开发）

### 🎯 目标
- 按照设计方案编写代码
- 保持代码质量和一致性

### 👤 角色分工
| 角色 | 任务 |
|------|------|
| **Codex** | 编写代码、运行测试、修复Bug |
| **Claude** | 代码审查、检查规范、提供建议 |
| **开发者** | 中间检查、决策疑难问题 |

### 📝 工作流程

#### 3.1 Codex 实现流程
1. **读取设计文档**
   ```bash
   # Codex 先读取
   cat workflow/steps/step2_design.md
   cat workflow/architecture.md  # 了解架构
   ```

2. **按设计逐步实现**
   - 按照 `step2_design.md` 的实现路径逐个完成
   - 每完成一个文件，在 `progress.md` 记录

3. **运行测试**
   ```bash
   # 每次修改后运行
   python3.11 -m pytest -q tests/test_xxx.py
   ```

4. **提交中间进度**
   ```bash
   # 追加到 progress.md
   echo "- $(date +%H:%M) [Codex] 完成 src/xxx.py 实现" >> workflow/progress.md
   ```

#### 3.2 Claude 审查流程
1. **定期审查代码**
   ```bash
   # Claude 读取 Codex 提交的代码
   git diff HEAD~1 src/
   ```

2. **检查清单**
   - [ ] 代码符合项目风格（PEP8、类型提示）
   - [ ] 错误处理完备
   - [ ] 日志记录适当
   - [ ] 测试覆盖充分
   - [ ] 文档字符串完整

3. **提出改进建议**
   ```markdown
   # Claude 在 progress.md 追加
   - [Claude审查] src/xxx.py:
     - ✅ 逻辑正确
     - ⚠️ 建议：增加异常处理
     - ⚠️ 建议：补充类型提示
   ```

#### 3.3 协作协议
- **Codex 优先实现**：先跑通功能，后优化细节
- **Claude 延迟审查**：每完成一个模块后审查，不打断 Codex 流程
- **争议升级**：Codex 和 Claude 意见不一致时，由开发者裁定

### 📝 输出物
1. **文件：** `workflow/steps/step3_implementation.md`
2. **内容：**
   ```markdown
   # Step 3: Implementation - [功能名称]

   ## 实现清单
   - [x] 修改 `src/xxx.py`
   - [x] 新增 `src/yyy.py`
   - [x] 更新 `tests/test_zzz.py`

   ## 实现亮点
   - [描述关键实现细节]

   ## 偏离设计的调整
   - 原设计：[xxx]
   - 实际实现：[yyy]
   - 原因：[zzz]

   ## 遗留问题
   - [ ] 待优化的性能瓶颈
   - [ ] 待补充的边界测试
   ```

### ✅ 检查门槛
- [ ] 所有设计文件已实现
- [ ] 本地测试通过（`pytest -q`）
- [ ] Claude 审查通过

### 🔄 状态更新
```bash
# Codex 开始实现
jq '.step = "step3_implementation" | .status = "in_progress" | .owner = "codex"' \
   workflow/state/current_step.json > tmp && mv tmp workflow/state/current_step.json

# 实现完成，等待验证
jq '.status = "completed" | .owner = "claude"' \
   workflow/state/current_step.json > tmp && mv tmp workflow/state/current_step.json
```

---

## 🧪 Step 4: Verification（验证测试）

### 🎯 目标
- 验证功能符合需求
- 确保未破坏现有功能

### 👤 角色分工
| 角色 | 任务 |
|------|------|
| **Claude** | 设计测试用例、运行回归测试、分析测试结果 |
| **Codex** | 修复测试失败、补充边界测试 |
| **开发者** | 人工验收、确认发布 |

### 📝 测试清单

#### 4.1 自动化测试（Claude + Codex）
```bash
# 1. 单元测试
python3.11 -m pytest -q

# 2. 集成测试
python3.11 -m pytest tests/test_orchestrator_scope_flow.py -v

# 3. E2E测试（如果涉及UI）
python3.11 -m pytest tests/e2e/test_frontend_playwright.py -v

# 4. 服务健康检查
bash scripts/start_all.sh restart
curl -sS http://127.0.0.1:9000/api/health
curl -sS http://127.0.0.1:8866/health
curl -sS http://127.0.0.1:8877/health
```

#### 4.2 验证清单（对照 step1 验收标准）
```markdown
# Claude 填写
## 验收标准验证
- [x] 标准1：[验证方法 + 结果]
- [x] 标准2：[验证方法 + 结果]

## 回归测试
- [x] 所有单元测试通过
- [x] 主流程未受影响
- [x] 性能未明显下降

## 已知问题
- [ ] 问题1：[描述 + 影响评估 + 是否阻塞发布]
```

### 📝 输出物
1. **文件：** `workflow/steps/step4_verification.md`
2. **测试截图：** `workflow/artifacts/test_results_[日期].png`
3. **内容模板：**
   ```markdown
   # Step 4: Verification - [功能名称]

   ## 测试执行
   - 测试时间：2026-03-04 14:30
   - 执行人：Claude

   ## 测试结果
   ### 单元测试
   ```
   30 passed in 5.2s
   ```

   ### 功能测试
   | 测试项 | 结果 | 备注 |
   |--------|------|------|
   | 功能A | ✅ PASS | - |
   | 功能B | ✅ PASS | - |

   ### 回归测试
   - ✅ 原有功能未受影响

   ## 已知问题
   无

   ## 发布建议
   ✅ 可以发布
   ```

### ✅ 检查门槛
- [ ] 所有验收标准通过
- [ ] pytest 全绿
- [ ] 服务健康检查通过
- [ ] 无阻塞性问题

### 🔄 状态更新
```bash
# 验证通过
jq '.step = "step4_verification" | .status = "passed" | .owner = "human"' \
   workflow/state/current_step.json > tmp && mv tmp workflow/state/current_step.json
```

---

## 🚀 Step 5: Release（发布复盘）

### 🎯 目标
- 合并代码、更新文档
- 总结经验、识别改进点

### 👤 角色分工
| 角色 | 任务 |
|------|------|
| **开发者** | 决定是否发布、合并代码 |
| **Claude** | 更新架构文档、编写复盘总结 |
| **Codex** | 无参与（等待下次迭代） |

### 📝 发布流程

#### 5.1 文档更新（Claude）
```bash
# 1. 更新架构文档
# 如果新增了模块或修改了流程，同步到 architecture.md

# 2. 更新实施计划
# 完成的待办项打勾，新增的债务追加

# 3. 追加执行日志
echo "## $(date +%Y-%m-%d)" >> workflow/progress.md
echo "- 完成功能：[功能名称]" >> workflow/progress.md
echo "- 修改文件：[列表]" >> workflow/progress.md
echo "- 测试结果：全部通过" >> workflow/progress.md
```

#### 5.2 Git 提交（开发者或 Claude）
```bash
# 提交代码（包含 workflow 文件）
git add src/ tests/ workflow/
git commit -m "[step5] 完成 [功能名称]

- 需求：[简述]
- 实现：[简述]
- 测试：pytest 30 passed

参考：workflow/steps/step1-5_*.md
"
```

#### 5.3 复盘总结（Claude）
```markdown
# Step 5: Release - [功能名称]

## 发布摘要
- 发布时间：2026-03-04 15:00
- 涉及文件：3个
- 代码行数：+150/-20
- 测试覆盖：100%

## 效能分析
- 实际耗时：4小时
- 计划耗时：3小时
- 偏差原因：测试用例补充超时

## 经验总结
### 做得好的
- 需求澄清充分，无返工
- 设计方案获得一次批准

### 可以改进
- 边界条件考虑不足，测试阶段补充
- 性能测试缺失

## 后续行动
- [ ] 补充性能基准测试
- [ ] 文档中增加使用示例

## 技术债务
- 配置脱敏改造（延续）
```

### ✅ 检查门槛
- [ ] 代码已合并到主分支
- [ ] `architecture.md` 已更新
- [ ] `progress.md` 已追加日志
- [ ] 复盘文档已完成

### 🔄 状态重置（准备下次迭代）
```bash
# Claude 执行
cat > workflow/state/current_step.json <<EOF
{
  "step": "idle",
  "status": "waiting",
  "task": null,
  "owner": null,
  "updated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
```

---

## 🔧 协作工具与约定

### 1. 状态文件规范

#### `workflow/state/current_step.json`
```json
{
  "step": "step1_requirements",      // 当前步骤（idle/step1-5）
  "status": "in_progress",           // 状态（in_progress/pending_approval/approved/completed/passed/waiting）
  "task": "添加视频水印功能",          // 当前任务名
  "owner": "claude",                 // 当前负责人（claude/codex/human）
  "updated_at": "2026-03-04T07:00:00Z"
}
```

#### `workflow/state/run_context.json`（可选）
```json
{
  "iteration": 5,                    // 当前迭代次数
  "last_completed": "2026-03-03",    // 上次完成日期
  "blocked_by": null,                // 阻塞原因
  "notes": "等待设计方案批准"          // 备注
}
```

### 2. 文件命名约定

```
workflow/steps/step1_requirements_[功能名].md     # 多需求并行时使用
workflow/artifacts/test_results_20260304.png      # 带日期
workflow/artifacts/design_diagram_watermark.png   # 带功能名
```

### 3. 进度日志格式

```markdown
## 2026-03-04
- 08:00 [Claude] 启动需求澄清：视频水印功能
- 08:30 [Claude] 完成需求文档，等待确认
- 09:00 [Human] 批准需求，进入设计阶段
- 10:00 [Claude] 完成方案设计，提交 step2_design.md
- 10:30 [Human] 批准方案A，分配给 Codex 实现
- 11:00 [Codex] 开始实现 src/factory/watermark.py
- 12:00 [Codex] 完成核心逻辑，本地测试通过
- 14:00 [Claude] 代码审查通过，建议补充类型提示
- 14:30 [Codex] 完成优化，提交验证
- 15:00 [Claude] 验证通过，准备发布
- 15:30 [Human] 合并代码到 main
```

### 4. Commit Message 规范

```
[step1] 需求澄清：视频水印功能
[step2] 设计方案：基于FFmpeg滤镜实现水印
[step3] 实现水印处理器和测试用例
[step4] 验证通过：30个测试用例全绿
[step5] 发布：更新架构文档和复盘总结
```

---

## 🚨 异常处理协议

### 1. 需求变更（在 step1-2）
- **发现者**：Claude 或开发者
- **处理**：回退到 step1，重新澄清需求
- **记录**：在 `progress.md` 标注 `[需求变更]`

### 2. 设计缺陷（在 step3-4）
- **发现者**：Codex 或 Claude
- **处理**：
  - 小调整：直接修改，在 `step3_implementation.md` 记录偏离
  - 大调整：回退到 step2，重新设计
- **决策**：Claude 评估影响，开发者最终裁定

### 3. 测试失败（在 step4）
- **发现者**：Claude
- **处理**：
  - Bug修复：分配给 Codex，修复后重新验证
  - 设计缺陷：回退到 step2
- **记录**：在 `step4_verification.md` 追加失败原因和修复方案

### 4. 冲突解决
| 情况 | 决策者 |
|------|--------|
| Claude 和 Codex 意见不一致 | 开发者裁定 |
| 实现方式争议 | Claude 提供选项，开发者选择 |
| 技术债务是否偿还 | 开发者决定，Claude 评估影响 |

---

## 📊 质量门禁

### 每个步骤的强制检查

| 步骤 | 门禁条件 | 检查者 |
|------|----------|--------|
| Step 1 | 需求明确无歧义 + 验收标准可量化 | Claude → 开发者确认 |
| Step 2 | 方案可执行 + 影响评估完整 | Claude → 开发者批准 |
| Step 3 | 代码符合规范 + 本地测试通过 | Codex → Claude审查 |
| Step 4 | pytest全绿 + 验收标准全满足 | Claude → 开发者验收 |
| Step 5 | 文档已更新 + 复盘已完成 | Claude → 开发者归档 |

### 全局约束
- ❌ **禁止跳步**：必须按顺序完成 step1 → step5
- ❌ **禁止无文档实现**：step3 必须基于 step2 的设计
- ❌ **禁止未测试发布**：step5 必须 step4 通过
- ❌ **禁止不更新架构文档**：改动模块必须同步 `architecture.md`

---

## 🎓 最佳实践

### Claude 的最佳实践
1. **需求阶段**：用提问而非假设，避免过度设计
2. **设计阶段**：提供2-3个方案，明确推荐及理由
3. **审查阶段**：先肯定亮点，再提建议，避免打击 Codex 积极性
4. **验证阶段**：自动化优先，人工验收为辅
5. **复盘阶段**：客观总结，识别系统性问题

### Codex 的最佳实践
1. **实现阶段**：先跑通主流程，再补充边界
2. **测试阶段**：每修改一个文件就跑一次测试
3. **协作阶段**：遇到设计不明确时，先问 Claude 而非自行决定
4. **文档阶段**：修改代码时同步更新 docstring
5. **提交阶段**：每个小功能完成就追加 `progress.md`

### 开发者的最佳实践
1. **需求阶段**：提供背景和目标，而非具体实现
2. **设计阶段**：关注权衡取舍，而非技术细节
3. **实现阶段**：信任 AI 执行，只在关键点介入
4. **验证阶段**：亲自验收核心功能，边缘功能看测试报告
5. **发布阶段**：决定发布时机，而非等待"完美"

---

## 🔗 快速参考

### 常用命令速查

```bash
# 查看当前步骤
cat workflow/state/current_step.json

# 查看实施计划
cat workflow/implementation-plan.md

# 查看执行日志
tail -n 20 workflow/progress.md

# 运行最小测试
python3.11 -m pytest -q

# 更新步骤状态（示例）
jq '.step = "step3_implementation" | .status = "in_progress"' \
   workflow/state/current_step.json > tmp && mv tmp workflow/state/current_step.json

# 检查服务状态
bash scripts/start_all.sh status

# 查看Git状态
git status
```

### 文件位置速查

| 用途 | 路径 |
|------|------|
| 当前步骤 | `workflow/state/current_step.json` |
| 实施计划 | `workflow/implementation-plan.md` |
| 执行日志 | `workflow/progress.md` |
| 架构现状 | `workflow/architecture.md` |
| 测试手册 | `workflow/testing-playbook.md` |
| 步骤模板 | `workflow/steps/step1-5_*.md` |
| 测试证据 | `workflow/artifacts/*.png` |

---

## 📞 协作示例

### 示例1：添加视频水印功能

```markdown
## 用户输入
"我想给翻译后的视频加上水印，显示频道Logo"

## Claude 响应（Step 1）
我来帮你澄清需求：

Q1: 水印位置固定吗（如右下角），还是需要可配置？
Q2: 水印是静态图片还是动态文字？
Q3: 是否需要根据视频方向（横版/竖版）自动调整位置？
Q4: 是否所有视频都加水印，还是可以按任务配置？

## 开发者回答
A1: 右下角固定
A2: 静态PNG图片，带透明通道
A3: 是的，横版距离边缘50px，竖版距离边缘30px
A4: 默认都加，支持任务级别关闭

## Claude 响应（Step 2）
需求已明确，我开始设计方案...

[生成 step2_design.md]

方案推荐：基于FFmpeg overlay滤镜实现
- 修改 `src/factory/long_video_processor.py`
- 新增 `src/factory/watermark.py`
- 配置路径：`config/watermark.png`

## 开发者批准
方案通过，Codex开始实现

## Codex 响应（Step 3）
收到，开始实现...

[修改文件，运行测试，追加progress.md]

实现完成，等待审查

## Claude 响应（Step 4）
代码审查通过，开始验证...

[运行测试，填写 step4_verification.md]

验证通过，建议发布

## 开发者决策
合并代码

## Claude 响应（Step 5）
已更新架构文档，复盘完成
```

---

## 🎯 成功指标

### 流程健康度
- ✅ 每次迭代都有完整的 step1-5 文档
- ✅ `progress.md` 持续更新
- ✅ `architecture.md` 与代码同步
- ✅ pytest 始终保持全绿

### 协作效率
- ✅ Claude 和 Codex 无重复劳动
- ✅ 设计方案一次批准率 > 80%
- ✅ 代码审查通过率 > 90%
- ✅ 测试失败导致的返工 < 10%

### 交付质量
- ✅ 需求变更率 < 20%
- ✅ 发布后Bug率 < 5%
- ✅ 文档完整性 = 100%
- ✅ 技术债务可追溯

---

## 📚 附录：Vibe Coding 原则对照表

| Vibe Coding 原则 | video-factory 实践 |
|------------------|-------------------|
| 规划驱动 | 强制 step1-2，禁止跳步 |
| 模块化索引 | `architecture.md` + `workflow/steps/` |
| AI 为主 | Claude 设计，Codex 实现，人类决策 |
| 上下文管理 | `current_step.json` + `run_context.json` |
| 测试优先 | `testing-playbook.md` + pytest 门禁 |
| 持续迭代 | `progress.md` 记录每次迭代 |
| 活文档 | `architecture.md` 随代码同步更新 |

---

**最后更新**：2026-03-04
**维护者**：video-factory 团队
**版本**：v1.0
