# 路径A：分层流水线多 Coding Agent 协作方案

## 1. 目标
在同一代码仓内，让多个 Coding Agent 并行协作且结果可控，避免冲突、返工与质量波动。

## 2. 路径A定义（固定）
路径A采用“分层流水线”模式：
1. 需求澄清层（Planner）
2. 架构设计层（Architect）
3. 实现开发层（Implementer，可并行）
4. 质量保障层（Reviewer + Tester）
5. 集成发布层（Integrator）

## 3. 角色与职责
- Planner：拆解需求、定义验收标准、生成任务卡。
- Architect：确定边界、接口、目录与技术约束。
- Implementer：按任务卡编码，不越权改范围外模块。
- Reviewer：做代码审查，关注正确性、回归风险、安全问题。
- Tester：补充与执行测试，出具通过/失败结论。
- Integrator：合并结果、处理冲突、生成发布说明。

## 4. 协作原则
- 单一事实源：以任务卡和本目录规范为准。
- 小步提交：每次只处理一个可验证增量。
- 先契约后实现：接口/类型先定稿再并行开发。
- 先验证再合并：未过质量门禁不得进入集成。
- 可追溯：所有变更都需映射到任务卡编号。

## 5. 标准流程
1. Planner 创建任务卡（目标、范围、验收、风险）。
2. Architect 产出设计决策（接口、数据结构、影响面）。
3. Implementer 并行开发（按模块切分，避免重叠文件）。
4. Reviewer 审查并给出阻断/建议项。
5. Tester 执行测试并记录结果。
6. Integrator 统一合并，形成变更摘要。

## 6. 并行切分策略
- 按模块切分：UI、API、数据层、脚本分轨道。
- 按文件所有权：同一时段一个文件只归一个 Implementer。
- 按契约切分：先定义 DTO/接口，再并行实现。

## 7. 质量门禁（必须全部通过）
- 编译/构建通过。
- 单元测试通过。
- 关键路径冒烟通过。
- Reviewer 无阻断问题。
- 变更说明与回滚方案完整。

## 8. 沟通与交接
- 每个任务必须有“输入、输出、完成定义（DoD）”。
- 跨 Agent 交接使用模板（见 `agents/templates/`）。
- 所有争议回到 Architect 决策，必要时升级 Planner。

## 9. 目录规范（本方案落地）
- `AGENTS.md`：所有 Agent 的统一行为准则。
- `governance/AGENT_CONSTITUTION.md`：角色职责与边界。
- `governance/WORKFLOW_PATH_A.md`：流程、状态、交接规则。
- `governance/QUALITY_GATES.md`：质量门禁与检查项。
- `agents/templates/TASK_CARD.md`：任务卡模板。
- `agents/templates/HANDOFF_NOTE.md`：交接模板。

## 10. 实施建议（首周）
- Day 1：确认角色人选与模块所有权。
- Day 2：选 1 个真实需求试跑路径A。
- Day 3：复盘瓶颈（冲突点、等待点、返工点）。
- Day 4-5：固化规范并扩展到全项目。
