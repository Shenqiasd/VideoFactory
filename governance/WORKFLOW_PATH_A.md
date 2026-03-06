# Workflow Path A（分层流水线）

## 状态机
1. `BACKLOG`
2. `PLANNED`
3. `DESIGNED`
4. `IN_PROGRESS`
5. `IN_REVIEW`
6. `IN_TEST`
7. `READY_TO_MERGE`
8. `DONE`

## 流转规则
- `BACKLOG -> PLANNED`：Planner 完成任务卡。
- `PLANNED -> DESIGNED`：Architect 完成接口与边界。
- `DESIGNED -> IN_PROGRESS`：Implementer 领取并开发。
- `IN_PROGRESS -> IN_REVIEW`：代码和自测完成。
- `IN_REVIEW -> IN_TEST`：Reviewer 无阻断项。
- `IN_TEST -> READY_TO_MERGE`：Tester 全通过。
- `READY_TO_MERGE -> DONE`：Integrator 合并并记录。

## 交接SLA
- 同层交接响应：4小时内。
- 阻断问题反馈：1小时内。
- 超时升级：先 Architect，后 Planner。

## 冲突处理
- 接口冲突：Architect 裁决。
- 范围冲突：Planner 裁决。
- 质量争议：以 `QUALITY_GATES` 为准。
