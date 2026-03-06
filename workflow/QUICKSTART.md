# 🚀 协同开发快速启动

5分钟上手 Claude + Codex 协同工作流程

---

## 📋 开始新任务前

### 1. 检查当前状态
```bash
cat workflow/state/current_step.json
```

**如果显示 `"step": "idle"`**：可以开始新任务
**如果显示其他步骤**：先完成当前任务

---

## 🎯 五步快速流程

### Step 1️⃣：需求澄清（5-15分钟）
**谁来做**：Claude + 开发者

```bash
# Claude 创建需求文档
# 开发者：提出需求 → Claude：提问澄清 → 开发者：确认

# 更新状态
jq '.step = "step1_requirements" | .status = "in_progress" | .task = "功能名" | .owner = "claude"' \
   workflow/state/current_step.json > tmp && mv tmp workflow/state/current_step.json
```

**输出**：`workflow/steps/step1_requirements.md`

---

### Step 2️⃣：方案设计（15-30分钟）
**谁来做**：Claude（主导）→ 开发者（批准）

```bash
# Claude 探索代码库，编写设计文档
# 开发者：审查方案 → 批准

# 批准后更新状态
jq '.step = "step2_design" | .status = "approved" | .owner = "codex"' \
   workflow/state/current_step.json > tmp && mv tmp workflow/state/current_step.json
```

**输出**：`workflow/steps/step2_design.md`

---

### Step 3️⃣：代码实现（30-120分钟）
**谁来做**：Codex（主导）→ Claude（审查）

```bash
# Codex 执行
# 1. 读取设计
cat workflow/steps/step2_design.md

# 2. 按设计实现代码
# 3. 每完成一个文件，运行测试
python3.11 -m pytest -q tests/test_xxx.py

# 4. 记录进度
echo "- $(date +%H:%M) [Codex] 完成 src/xxx.py" >> workflow/progress.md

# 5. 完成后更新状态
jq '.status = "completed" | .owner = "claude"' \
   workflow/state/current_step.json > tmp && mv tmp workflow/state/current_step.json
```

**Claude 审查清单**：
- [ ] 代码风格符合 PEP8
- [ ] 类型提示完整
- [ ] 错误处理完备
- [ ] 测试覆盖充分

**输出**：代码 + `workflow/steps/step3_implementation.md`

---

### Step 4️⃣：验证测试（15-30分钟）
**谁来做**：Claude（主导）+ Codex（修Bug）

```bash
# Claude 执行
# 1. 运行所有测试
python3.11 -m pytest -q

# 2. 服务健康检查（如果改动了服务）
bash scripts/start_all.sh restart
curl http://127.0.0.1:9000/api/health

# 3. 对照 step1 验收标准逐条验证
# 4. 填写验证报告

# 验证通过更新状态
jq '.step = "step4_verification" | .status = "passed" | .owner = "human"' \
   workflow/state/current_step.json > tmp && mv tmp workflow/state/current_step.json
```

**输出**：`workflow/steps/step4_verification.md`

---

### Step 5️⃣：发布复盘（10-20分钟）
**谁来做**：开发者（决策）+ Claude（文档）

```bash
# 开发者：决定是否发布

# Claude 执行
# 1. 更新架构文档（如果有模块变更）
vim workflow/architecture.md

# 2. 追加执行日志
echo "## $(date +%Y-%m-%d)" >> workflow/progress.md
echo "- 完成功能：[功能名]" >> workflow/progress.md

# 3. 编写复盘总结
# 填写 workflow/steps/step5_release.md

# 4. 提交代码
git add .
git commit -m "[step5] 完成 [功能名]

参考：workflow/steps/step1-5_*.md
"

# 5. 重置状态
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

**输出**：`workflow/steps/step5_release.md` + Git commit

---

## 🚨 常见问题速查

### Q1: 如何知道当前谁该干活？
```bash
# 查看 owner 字段
jq '.owner' workflow/state/current_step.json

# claude   → Claude 负责（需求/设计/审查/验证）
# codex    → Codex 负责（实现/修Bug）
# human    → 开发者决策（批准设计/验收/发布）
```

### Q2: 测试失败了怎么办？
```bash
# 在 step4，测试失败
# Claude：分析失败原因 → 分配给 Codex 修复 → 重新验证

# 更新状态回到实现
jq '.step = "step3_implementation" | .status = "fixing_bugs" | .owner = "codex"' \
   workflow/state/current_step.json > tmp && mv tmp workflow/state/current_step.json
```

### Q3: 需求变了怎么办？
```bash
# 立即回退到 step1
jq '.step = "step1_requirements" | .status = "requirement_changed" | .owner = "claude"' \
   workflow/state/current_step.json > tmp && mv tmp workflow/state/current_step.json

# 在 progress.md 标注
echo "- $(date +%H:%M) [需求变更] 原因：..." >> workflow/progress.md
```

### Q4: Claude 和 Codex 意见不一致？
**升级给开发者裁定**：
1. Claude 说明建议理由
2. Codex 说明当前实现理由
3. 开发者做最终决策
4. 在 `progress.md` 记录决策过程

---

## 📊 质量门禁速查

| 步骤 | 必须通过才能进入下一步 |
|------|------------------------|
| Step 1 → 2 | ✅ 开发者确认需求无误 |
| Step 2 → 3 | ✅ 开发者批准方案 |
| Step 3 → 4 | ✅ 本地测试通过 + Claude审查通过 |
| Step 4 → 5 | ✅ pytest全绿 + 验收标准满足 |
| Step 5 → 结束 | ✅ 架构文档已更新 + 代码已合并 |

---

## 🎯 角色速查表

| 我是... | 我主要做... | 我的工具 |
|---------|-------------|----------|
| **Claude** | 需求分析、方案设计、代码审查、测试验证 | Read/Grep/Bash |
| **Codex** | 代码实现、单元测试、Bug修复 | Edit/Write/Bash |
| **开发者** | 需求确认、方案批准、最终验收 | Git/终端/浏览器 |

---

## 📂 关键文件位置

```
workflow/
├── COLLABORATION_GUIDE.md      # 完整协作规范（详细版）
├── QUICKSTART.md               # 快速启动（本文档）
├── state/
│   └── current_step.json       # 🔴 当前步骤状态（最常查看）
├── steps/
│   ├── step1_requirements.md   # 需求文档
│   ├── step2_design.md         # 设计文档
│   ├── step3_implementation.md # 实现总结
│   ├── step4_verification.md   # 测试报告
│   └── step5_release.md        # 发布复盘
├── progress.md                 # 🔴 执行日志（持续追加）
├── architecture.md             # 架构现状（随代码更新）
└── testing-playbook.md         # 测试命令手册
```

---

## 💡 三条黄金法则

1. **永远先看 `current_step.json`**：知道当前在哪一步，谁该干活
2. **禁止跳步**：必须按 step1 → step5 顺序完成
3. **文档先于代码**：step3 必须基于 step2 的设计

---

## 🔗 更多信息

- 详细协作规范：`workflow/COLLABORATION_GUIDE.md`
- 测试命令手册：`workflow/testing-playbook.md`
- 架构文档：`workflow/architecture.md`
- Vibe Coding 原文：https://github.com/tukuaiai/vibe-coding-cn

---

**开始第一个任务？**

1. 确认状态是 `idle`：`cat workflow/state/current_step.json`
2. 告诉 Claude 你的需求
3. Claude 会自动进入 step1，开始提问澄清
4. 按照五步流程走完即可 🎉

**Good luck!** 🚀
