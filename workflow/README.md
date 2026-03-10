# workflow 使用说明

这是 video-factory 的标准开发流程目录，遵循 [vibe-coding-cn](https://github.com/tukuaiai/vibe-coding-cn) 工程化理念。

---

## 🚀 快速开始

**新手？** 先看这两个文档：
1. **5分钟上手**：[QUICKSTART.md](./QUICKSTART.md) - 快速了解协作流程
2. **完整规范**：[COLLABORATION_GUIDE.md](./COLLABORATION_GUIDE.md) - Claude + Codex 协同开发详细指南

**老手？** 直接查看：
```bash
cat workflow/state/current_step.json  # 当前步骤和负责人
cat workflow/progress.md | tail -20    # 最近进展
```

---

## 📂 目录结构

```
workflow/
├── COLLABORATION_GUIDE.md      # Claude + Codex 协同规范（必读）
├── QUICKSTART.md               # 5分钟快速启动指南
├── README.md                   # 本文档
│
├── state/                      # 状态追踪
│   ├── current_step.json       # 当前步骤、状态、负责人
│   └── run_context.json        # 运行上下文（可选）
│
├── steps/                      # 五步文档模板
│   ├── step1_requirements.md   # 需求澄清
│   ├── step2_design.md         # 方案设计
│   ├── step3_implementation.md # 实现开发
│   ├── step4_verification.md   # 验证测试
│   └── step5_release.md        # 发布复盘
│
├── artifacts/                  # 验证证据
│   ├── test_results_*.png      # 测试截图
│   └── design_diagram_*.png    # 设计图
│
├── implementation-plan.md      # 当前迭代计划
├── progress.md                 # 执行日志（持续追加）
├── architecture.md             # 架构现状（活文档）
├── testing-playbook.md         # 测试作业手册
├── runner.md                   # 半自动执行建议
└── hooks.md                    # 开发钩子建议
```

---

## 🔄 标准五步流程

```
┌─────────────────────────────────────────────────────────┐
│  Step 1: Requirements  → Claude 主导，开发者确认         │
│  Step 2: Design        → Claude 主导，开发者批准         │
│  Step 3: Implementation→ Codex 主导，Claude 审查        │
│  Step 4: Verification  → Claude + Codex 联合            │
│  Step 5: Release       → Claude 总结，开发者归档         │
└─────────────────────────────────────────────────────────┘
```

**详细说明**：参见 [COLLABORATION_GUIDE.md](./COLLABORATION_GUIDE.md)

---

## 🎯 每日工作流

### 开发者（人类）
```bash
# 1. 检查当前状态
cat workflow/state/current_step.json

# 2. 查看待办事项
cat workflow/implementation-plan.md

# 3. 提出需求或批准设计
# （与 Claude/Codex 对话）

# 4. 查看进展
tail -20 workflow/progress.md
```

### Claude
```bash
# 1. 读取当前状态
cat workflow/state/current_step.json

# 2. 执行对应步骤任务
# - Step 1/2: 需求澄清和方案设计
# - Step 4: 测试验证
# - Step 5: 文档更新和复盘

# 3. 更新进度
echo "- $(date +%H:%M) [Claude] 完成XXX" >> workflow/progress.md

# 4. 更新状态
jq '.status = "completed"' workflow/state/current_step.json > tmp && mv tmp workflow/state/current_step.json
```

### Codex (Cursor)
```bash
# 1. 读取设计文档
cat workflow/steps/step2_design.md
cat workflow/architecture.md

# 2. 实现代码
# （按设计逐步实现）

# 3. 运行测试
./.venv/bin/python -m pytest -q

# 4. 记录进度
echo "- $(date +%H:%M) [Codex] 完成 src/xxx.py" >> workflow/progress.md
```

---

## ⚠️ 强制规则

1. **禁止跳步**：必须按 step1 → step5 顺序完成
2. **禁止无设计实现**：step3 必须基于 step2 的批准方案
3. **禁止未测试发布**：step5 必须 step4 验证通过
4. **禁止不更新文档**：改动模块必须同步 `architecture.md`

**违反规则？** → 立即回退到正确步骤

---

## 🚨 异常处理

| 情况 | 处理方式 |
|------|----------|
| 需求变更 | 回退到 step1，重新澄清 |
| 设计缺陷 | 回退到 step2，重新设计 |
| 测试失败 | 停留在 step4，修复后重新验证 |
| 意见冲突 | 升级给开发者裁定 |

---

## 📊 质量门禁

每个步骤必须满足条件才能进入下一步：

| 步骤 | 门禁条件 |
|------|----------|
| Step 1 → 2 | ✅ 需求明确 + 验收标准可量化 + 开发者确认 |
| Step 2 → 3 | ✅ 方案可执行 + 影响评估完整 + 开发者批准 |
| Step 3 → 4 | ✅ 本地测试通过 + Claude 审查通过 |
| Step 4 → 5 | ✅ pytest 全绿 + 验收标准满足 + 开发者验收 |
| Step 5 → 结束 | ✅ 架构文档已更新 + 代码已合并 |

---

## 📚 参考文档

- **协作规范**：[COLLABORATION_GUIDE.md](./COLLABORATION_GUIDE.md)
- **快速启动**：[QUICKSTART.md](./QUICKSTART.md)
- **架构现状**：[architecture.md](./architecture.md)
- **测试手册**：[testing-playbook.md](./testing-playbook.md)
- **实施计划**：[implementation-plan.md](./implementation-plan.md)
- **Vibe Coding 原文**：https://github.com/tukuaiai/vibe-coding-cn

---

## 与现有脚本协作
- 启停服务：`bash scripts/start_all.sh start|stop|status|logs`
- API 服务：`./.venv/bin/python scripts/start_server.py`
- Worker：`./.venv/bin/python workers/main.py`
- 全量测试：`./.venv/bin/python -m pytest -q`

---

**有问题？** 先看 [QUICKSTART.md](./QUICKSTART.md) 的常见问题部分 🚀
