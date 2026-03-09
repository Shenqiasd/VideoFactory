# task-014: 表单提交

实现表单提交逻辑：验证必填字段（视频、平台、标题），调用POST /api/distribute/publish API，成功后跳转到发布页面 [REMEMBER] 使用Alpine.js的@submit.prevent拦截scope=full时的表单提交，仅在全流程模式下触发发布API [DECISION] 采用最小化验证策略（仅检查必填项），复用现有showToast反馈机制，成功后重定向到/publish页面
