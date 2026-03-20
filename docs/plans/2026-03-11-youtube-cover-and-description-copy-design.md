# YouTube Cover And Description Copy Design

**目标**

- 加工产物中的封面优先使用 YouTube 原始缩略图，不再输出多张截图封面。
- 任务详情页为翻译后视频简介提供直接复制入口。

**设计**

- 封面生成流程优先判断 `task.source_url` 是否为 YouTube 链接；若是，则先取远程缩略图并将其保存为唯一封面产物。
- 若远程缩略图不可用，则回退到现有截帧逻辑，但只保留一张最终封面文件，不再把最佳帧和多尺寸封面全部暴露到输出目录。
- `FactoryPipeline` 仍然把封面作为 `cover` 类型产物记录，但只记录一个条目，避免任务下载区出现多张封面。
- 任务详情页在“项目信息与质检”区域新增“翻译简介”展示块和复制按钮，复制内容取 `translated_description`。

**影响范围**

- `src/factory/cover.py`
- `src/factory/pipeline.py`
- `web/templates/task_detail.html`
- 相关测试文件
