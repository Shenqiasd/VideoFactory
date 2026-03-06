# hooks 建议

可选引入本地 Git Hook（或 Claude/Codex Hook）做流程守护：

## pre-commit 建议
- 校验 `workflow/state/current_step.json` 合法 JSON
- 若改动了 `src/` 或 `api/`，提示同步 `workflow/progress.md`

## pre-push 建议
- 强制运行：`python3.11 -m pytest -q`
- 若失败，阻止推送

## commit-msg 建议
- 要求包含：`[stepX]` 标签，追踪当前流程步骤
