# runner 建议（半自动）

当前仓库尚未内置 workflow runner 脚本；先使用人工+约定方式执行：

1. 开始任务时
- 修改 `workflow/state/current_step.json`
- 在对应 `workflow/steps/step*.md` 填写本轮内容

2. 实施中
- 每完成一个子项，追加 `workflow/progress.md`

3. 收尾
- 执行 `workflow/testing-playbook.md` 中最小必跑项
- 在 `workflow/steps/step5_release.md` 填写结果

后续可新增 `workflow/runner.py` 自动推进步骤与状态文件。
